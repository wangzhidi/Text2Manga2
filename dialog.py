import cv2
from ultralytics import YOLO
from PIL import Image
from speech_bubble import draw_speech_bubble, draw_text, draw_rect
import cairo
import torch
from transformers import CLIPProcessor, CLIPModel
import numpy as np
import json
import os
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import threading
from data import *

BASE_PATH = Path(__file__).parent

# 为CLIP模型加载添加全局锁，防止多线程竞争
_clip_model_lock = threading.Lock()
_clip_model_cache = {}
_clip_processor_cache = {}

# YOLO模型缓存
_yolo_model_lock = threading.Lock()
_yolo_model_cache = {}
# YOLO推理锁（ultralytics模型在多线程并发调用时偶发不稳定）
_yolo_infer_lock = threading.Lock()

# HuggingFace网络较慢时，默认10秒的HEAD超时容易触发ReadTimeout
# 这里在未显式配置时提高超时时间，减少偶发失败
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "60")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "120")


def _create_cairo_surface_from_image(image_path: str):
    """从任意常见位图（jpg/png等）构建可绘制的 cairo surface。"""
    pil_img = Image.open(image_path).convert("RGBA")
    width, height = pil_img.size
    # cairo ARGB32 对应 BGRA 字节序
    img_data = bytearray(pil_img.tobytes("raw", "BGRA"))
    surface = cairo.ImageSurface.create_for_data(
        img_data,
        cairo.FORMAT_ARGB32,
        width,
        height,
        width * 4,
    )
    return surface, img_data


def _save_cairo_surface_as_jpg(surface: cairo.ImageSurface, output_path: str, quality: int = 95):
    """将 cairo surface 按 JPEG 格式保存。"""
    width, height = surface.get_width(), surface.get_height()
    raw = bytes(surface.get_data())
    img = Image.frombytes("RGBA", (width, height), raw, "raw", "BGRA")
    img.convert("RGB").save(output_path, format="JPEG", quality=quality, optimize=True)


def _get_or_load_clip(model_name: str, device: str, allow_fallback: bool = True):
    """
    线程安全地加载并缓存CLIP模型与Processor。
    加载策略：
    1) 优先本地缓存（local_files_only=True），避免每次联网HEAD请求
    2) 本地无缓存时再走在线下载
    3) 若anime CLIP在线失败，可回退到openai/clip-vit-base-patch32
    """
    global _clip_model_cache, _clip_processor_cache

    with _clip_model_lock:
        if model_name in _clip_model_cache and model_name in _clip_processor_cache:
            return _clip_model_cache[model_name], _clip_processor_cache[model_name]

        # 1) 先尝试离线加载（如果本地已有缓存，这一步最稳）
        try:
            model = CLIPModel.from_pretrained(model_name, local_files_only=True).to(device).eval()
            processor = CLIPProcessor.from_pretrained(model_name, local_files_only=True, use_fast=True)
            _clip_model_cache[model_name] = model
            _clip_processor_cache[model_name] = processor
            return model, processor
        except Exception:
            pass

        # 2) 再尝试在线加载
        try:
            model = CLIPModel.from_pretrained(model_name).to(device).eval()
            processor = CLIPProcessor.from_pretrained(model_name, use_fast=True)
            _clip_model_cache[model_name] = model
            _clip_processor_cache[model_name] = processor
            return model, processor
        except Exception as e:
            # 3) anime CLIP失败时可回退到openai CLIP，保证流程不中断
            fallback_model_name = "openai/clip-vit-base-patch32"
            if allow_fallback and model_name != fallback_model_name:
                try:
                    model = CLIPModel.from_pretrained(fallback_model_name).to(device).eval()
                    processor = CLIPProcessor.from_pretrained(fallback_model_name, use_fast=True)
                    _clip_model_cache[fallback_model_name] = model
                    _clip_processor_cache[fallback_model_name] = processor
                    print(f"[WARN] 加载 {model_name} 失败，已回退到 {fallback_model_name}。错误: {e}")
                    return model, processor
                except Exception as fallback_e:
                    raise RuntimeError(
                        f"加载CLIP模型失败: {model_name}; fallback: {fallback_model_name}; 原始错误: {e}; 回退错误: {fallback_e}"
                    )

            raise RuntimeError(f"加载CLIP模型失败: {model_name}; 错误: {e}")



def detect_face(image_path, min_area_ratio=0.005):
    """
    使用YOLOv8模型检测图片中的人物，并返回一个包含边界框和裁剪后图像的列表。
    """
    model_path = r"models\yolov8x6_animeface.pt"
    with _yolo_model_lock:
        if model_path not in _yolo_model_cache:
            _yolo_model_cache[model_path] = YOLO(model_path)
        model = _yolo_model_cache[model_path]
    img = cv2.imread(image_path)
    if img is None:
        return []

    # 说明：YOLO在多线程并发推理时偶发出现
    # "'Conv' object has no attribute 'bn'"，这里串行化推理并在异常时重载一次。
    try:
        with _yolo_infer_lock:
            results = model(img)
    except AttributeError as e:
        if "has no attribute 'bn'" not in str(e):
            raise
        print(f"[WARN] YOLO推理异常，尝试重载模型后重试: {e}")
        with _yolo_model_lock:
            _yolo_model_cache[model_path] = YOLO(model_path)
            model = _yolo_model_cache[model_path]
        with _yolo_infer_lock:
            results = model(img)

    face_data = []
    id = 0
    for r in results:
        boxes = r.boxes
        for box in boxes:
            if model.names[int(box.cls[0])] == "face":
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                bbox_area = (x2 - x1) * (y2 - y1)
                img_height, img_width, _ = img.shape
                total_image_area = img_height * img_width
                # 过滤掉像素占比过小的结果
                if (bbox_area / total_image_area) >= min_area_ratio:
                    # 按原bbox宽高比例扩展
                    width = x2 - x1
                    height = y2 - y1
                    x1_expanded = int(max(0, x1 - 0.2 * width))
                    y1_expanded = int(max(0, y1 - 0.6 * height))
                    x2_expanded = int(min(img_width, x2 + 0.2 * width))
                    y2_expanded = int(min(img_height, y2 + 0.1 * height))
                    
                    cropped_img = Image.fromarray(cv2.cvtColor(img[y1_expanded:y2_expanded, x1_expanded:x2_expanded], cv2.COLOR_BGR2RGB))
                    
                    face_data.append({
                        "id": id,
                        "bbox": (x1_expanded, y1_expanded, x2_expanded, y2_expanded),
                        "cropped_image": cropped_img
                    })
                    id += 1
    
    return face_data

def find_most_similar_face(cropped_images_data, text_description):
    """
    使用 CLIP 模型找到与给定文本描述最相似的人物图像。
    返回包含匹配信息和相似度的字典，同时为所有人脸添加相似度分数。
    """
    if not cropped_images_data:
        return None
    if len(cropped_images_data) == 1:
        result = cropped_images_data[0].copy()
        result['similarity_score'] = 1.0
        return result
    
    # 确定设备
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    model, processor = _get_or_load_clip("openai/clip-vit-base-patch32", device)

    # 处理文本输入
    inputs = processor(text=text_description, images=None, return_tensors="pt", padding=True)
    input_ids = inputs.input_ids.to(device)
    with torch.no_grad():
        text_features = model.get_text_features(input_ids=input_ids)
    
    # 处理图像输入
    image_list = [data['cropped_image'] for data in cropped_images_data]
    inputs = processor(text=None, images=image_list, return_tensors="pt", padding=True)
    pixel_values = inputs.pixel_values.to(device)
    with torch.no_grad():
        image_features = model.get_image_features(pixel_values=pixel_values)

    # 计算余弦相似度
    similarity_scores = torch.matmul(text_features, image_features.T).squeeze(0)
    best_match_index = torch.argmax(similarity_scores).item()
    best_match_score = similarity_scores[best_match_index].item()
    
    # 为所有人脸添加相似度分数（以百分比形式，0-100）
    for i, data in enumerate(cropped_images_data):
        data['similarity_score'] = similarity_scores[i].item() * 100
    
    result = cropped_images_data[best_match_index].copy()
    result['similarity_score'] = best_match_score * 100
    result['query'] = text_description
    return result
def get_all_face_speaker_similarities(detected_people, all_speakers):
    """
    使用 Anime CLIP 计算所有检测到的人脸与所有候选说话人的相似度矩阵。
    为每个人脸添加 speaker_similarities 字典。
    """
    if not detected_people or not all_speakers:
        return None
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    MODEL_NAME = "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"   # 🔥 anime CLIP

    model, processor = _get_or_load_clip(MODEL_NAME, device, allow_fallback=True)
    
    # =========================
    # 1️⃣ 图像编码（一次性 batch）
    # =========================
    image_list = [data['cropped_image'] for data in detected_people]
    image_inputs = processor(images=image_list, return_tensors="pt", padding=True)
    pixel_values = image_inputs.pixel_values.to(device)

    with torch.no_grad():
        image_features = model.get_image_features(pixel_values=pixel_values)

    # 🔥 关键：归一化
    image_features = image_features / image_features.norm(dim=-1, keepdim=True)

    # =========================
    # 2️⃣ 文本编码（一次性 batch）
    # =========================
    text_inputs = processor(text=all_speakers, return_tensors="pt", padding=True)
    input_ids = text_inputs.input_ids.to(device)
    attention_mask = text_inputs.attention_mask.to(device)

    with torch.no_grad():
        text_features = model.get_text_features(
            input_ids=input_ids,
            attention_mask=attention_mask
        )

    # 🔥 关键：归一化
    text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    # =========================
    # 3️⃣ 相似度计算
    # =========================
    logit_scale = model.logit_scale.exp()
    similarity = torch.matmul(image_features, text_features.T) * logit_scale

    similarity = similarity.detach().cpu().numpy()


    # =========================
    # 4️⃣ 组织输出
    # =========================
    similarities_matrix = {}

    for speaker_idx, speaker in enumerate(all_speakers):
        similarities_matrix[speaker] = similarity[:, speaker_idx].tolist()

    # 写回每个人脸
    for person_idx, person in enumerate(detected_people):
        person['speaker_similarities'] = {}
        for speaker_idx, speaker in enumerate(all_speakers):
            person['speaker_similarities'][speaker] = similarity[person_idx][speaker_idx]

    return similarities_matrix


def calculate_iou(box1, box2):
    """
    计算两个矩形框的交并比 (Intersection over Union)。
    """
    x1, y1, x2, y2 = box1
    x1_prime, y1_prime, x2_prime, y2_prime = box2
    
    inter_x1 = max(x1, x1_prime)
    inter_y1 = max(y1, y1_prime)
    inter_x2 = min(x2, x2_prime)
    inter_y2 = min(y2, y2_prime)
    
    inter_area = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
    
    area1 = (x2 - x1) * (y2 - y1)
    area2 = (x2_prime - x1_prime) * (y2_prime - y1_prime)
    
    union_area = area1 + area2 - inter_area
    
    return inter_area / union_area if union_area > 0 else 0.0

def calculate_intersection_area(box1, box2):
    """只计算重叠面积，用于Sound Effect简单的避让"""
    x1, y1, x2, y2 = box1
    x1_prime, y1_prime, x2_prime, y2_prime = box2
    
    inter_x1 = max(x1, x1_prime)
    inter_y1 = max(y1, y1_prime)
    inter_x2 = min(x2, x2_prime)
    inter_y2 = min(y2, y2_prime)
    
    return max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)

def get_random_se_position(image_size, box_size, existing_obstacles):
    """
    为Sound Effect生成随机位置，尝试避开现有障碍物。
    """
    img_width, img_height = image_size
    box_w, box_h = box_size
    
    # 定义尝试次数
    attempts = 20
    best_box = None
    min_overlap = float('inf')
    
    for _ in range(attempts):
        # 随机选择左侧或右侧区域 (中间50%高度区域)
        side = random.choice(['left', 'right'])
        
        # 定义生成区域的中心范围
        if side == 'left':
            center_x_min = img_width * 0.1
            center_x_max = img_width * 0.2
        else:
            center_x_min = img_width * 0.8
            center_x_max = img_width * 0.9
            
        center_y_min = img_height * 0.3
        center_y_max = img_height * 0.7
        
        # 随机生成中心点
        cx = random.uniform(center_x_min, center_x_max)
        cy = random.uniform(center_y_min, center_y_max)
        
        # 计算左上角，确保不越界
        x1 = int(cx - box_w / 2)
        y1 = int(cy - box_h / 2)
        
        # 边界修正
        x1 = max(10, min(x1, img_width - box_w - 10))
        y1 = max(10, min(y1, img_height - box_h - 10))
        x2 = x1 + box_w
        y2 = y1 + box_h
        
        candidate_box = (x1, y1, x2, y2)
        
        # 计算与所有障碍物的重叠面积总和
        current_overlap = 0
        for obstacle in existing_obstacles:
            current_overlap += calculate_intersection_area(candidate_box, obstacle)
            
        if current_overlap == 0:
            return candidate_box, (cx, cy) # 找到无重叠位置直接返回
            
        if current_overlap < min_overlap:
            min_overlap = current_overlap
            best_box = candidate_box

    # 如果尝试多次都有重叠，返回重叠最小的那个
    if best_box:
        cx = (best_box[0] + best_box[2]) / 2
        cy = (best_box[1] + best_box[3]) / 2
        return best_box, (cx, cy)
    
    # 兜底
    return (10, 10, 10 + box_w, 10 + box_h), (10 + box_w/2, 10 + box_h/2)

def find_best_dialogue_box_position(
    speaker_bbox, 
    all_obstacles,
    dialogue_box_size, 
    image_size, 
    weights=(0.01, 0.99),
    only_corners=False
):
    img_width, img_height = image_size
    box_width, box_height = dialogue_box_size
    
    speaker_x = (speaker_bbox[0] + speaker_bbox[2]) / 2
    speaker_y = (speaker_bbox[1] + speaker_bbox[3]) / 2
    
    speaker_w = speaker_bbox[2] - speaker_bbox[0]
    speaker_h = speaker_bbox[3] - speaker_bbox[1]

    if only_corners:
        # 只使用四角的候选位置，用于说话人不在屏幕中的情况
        candidates = [
            (box_width/2 + 20, box_height/2 + 20),
            (img_width-box_width/2 - 20, box_height/2 + 20),
            (box_width/2 + 20, img_height-box_height/2 - 20),
            (img_width-box_width/2 - 20, img_height-box_height/2 - 20),
        ]
        
        best_score = -float('inf')
        best_position = None
        best_corner = None
        
        for cx, cy in candidates:
            x = cx - box_width / 2
            y = cy - box_height / 2
            
            x = max(0, min(x, img_width - box_width))
            y = max(0, min(y, img_height - box_height))
            
            candidate_bbox = (int(x), int(y), int(x + box_width), int(y + box_height))
            
            # 检查与障碍物的重叠
            max_iou = 0.0
            for obs_bbox in all_obstacles:
                iou = calculate_iou(candidate_bbox, obs_bbox)
                if iou > max_iou:
                    max_iou = iou
            
            score = -max_iou * 10  # 重叠最少的得分最高
            
            if score > best_score:
                best_score = score
                best_position = candidate_bbox
                best_corner = (cx, cy)
        
        if best_position is None:
            best_position = (0, 0, int(box_width), int(box_height))
            best_corner = (box_width/2, box_height/2)
        
        # 箭头指向图像四角边缘
        arrow_pos = best_corner
        return best_position, arrow_pos
    
    # 原有的逻辑
    # 定义候选位置：相对于说话人头部
    # 增加候选位置数量和偏移距离，防止同一角色多个对话框重叠
    candidates = [
        # 上方（多个位置）
        (speaker_x - (speaker_w/2+box_width/2)-50, speaker_y - (speaker_h/2+box_height/2)-50),
        (speaker_x, speaker_y - (speaker_h/2+box_height/2)-80),
        (speaker_x + (speaker_w/2+box_width/2)+50, speaker_y - (speaker_h/2+box_height/2)-50),
        # 左侧（多个位置）
        (box_width/2 + 20, speaker_y - (speaker_h/2+box_height/2)-100),
        (box_width/2 + 20, speaker_y - (speaker_h/2+box_height/2)),
        (box_width/2 + 20, speaker_y + (speaker_h/2+box_height/2)),
        (box_width/2 + 20, speaker_y + (speaker_h/2+box_height/2)+100),
        # 右侧（多个位置）
        (img_width-box_width/2 - 20, speaker_y - (speaker_h/2+box_height/2)-100),
        (img_width-box_width/2 - 20, speaker_y - (speaker_h/2+box_height/2)),
        (img_width-box_width/2 - 20, speaker_y + (speaker_h/2+box_height/2)),
        (img_width-box_width/2 - 20, speaker_y + (speaker_h/2+box_height/2)+100),
        # 四角
        (box_width/2 + 20, box_height/2 + 20),
        (img_width-box_width/2 - 20, box_height/2 + 20),
        (box_width/2 + 20, img_height-box_height/2 - 20),
        (img_width-box_width/2 - 20, img_height-box_height/2 - 20),
        # 额外位置（处理多个对话框的情况）
        (img_width/2, box_height/2 + 20),
        (img_width/2, img_height - box_height/2 - 20),
    ]

    best_score = -float('inf')
    best_position = None
    
    for cx, cy in candidates:
        # 将中心点坐标转换为左上角坐标，并修正边界
        x = cx - box_width / 2
        y = cy - box_height / 2
        
        # 确保对话框在图像范围内
        x = max(0, min(x, img_width - box_width))
        y = max(0, min(y, img_height - box_height))
        
        candidate_bbox = (int(x), int(y), int(x + box_width), int(y + box_height))
        
    
        # 计算遮挡惩罚：与所有障碍物（人和其他对话框）的最大IoU
        max_iou = 0.0
        for obs_bbox in all_obstacles:
            iou = calculate_iou(candidate_bbox, obs_bbox)
            if iou > max_iou:
                max_iou = iou
        
        # 计算距离得分：离中心越近距离分越高
        center_dist = np.sqrt((x + box_width/2 - speaker_x)**2 + (y + box_height/2 - speaker_y)**2)
        max_dist = np.sqrt(img_width**2 + img_height**2)
        distance_score = 1 - (center_dist / max_dist)
        
        # 综合得分
        w_distance, w_iou = weights
        # 计算综合得分：距离得分减去遮挡惩罚
        distance_score_weighted = w_distance * distance_score
        iou_penalty = w_iou * max_iou * 10
        score = distance_score_weighted - iou_penalty
        
        if score > best_score:
            best_score = score
            best_position = candidate_bbox
    
    # 如果没有找到合适位置（极少情况），默认左上角
    if best_position is None:
        best_position = (0, 0, int(box_width), int(box_height))

    # 箭头指向说话人中心
    arrow_pos = (speaker_x, speaker_y)
    return best_position, arrow_pos


def process_item(item, book_id, chapter, output_base_dir):
    """处理单个item的所有图片和文本"""
    try:
        # 处理 image_path 列表中的每张图片
        image_paths = item.get('image_path', [])
        if isinstance(image_paths, str):
            image_paths = [image_paths]  # 兼容旧格式

        if not image_paths:
            return f"⊘ ID {item.get('id')} 没有图片路径，跳过。"

        # 检查是否需要检测人脸
        text_types = item.get('text_type', [])
        needs_face_detection = any(t in ["dialogue", "monologue"] for t in text_types)

        output_dir = Path(output_base_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        for img_idx, image_path_rel in enumerate(image_paths):
            # 统一从项目根目录解析，避免依赖当前工作目录
            rel_norm = str(image_path_rel).replace('\\', '/').lstrip('/')
            if os.path.isabs(str(image_path_rel)):
                image_path = str(Path(image_path_rel))
            elif rel_norm.startswith('image/'):
                image_path = str((BASE_PATH / rel_norm).resolve())
            else:
                image_path = str((BASE_PATH / 'image' / book_id / chapter / Path(rel_norm).name).resolve())

            if not os.path.exists(image_path):
                continue

            try:
                image = cv2.imread(image_path)
                if image is None:
                    continue
                img_h, img_w = image.shape[:2]
            except Exception as e:
                continue

            # 人脸检测
            detected_people = []
            if needs_face_detection:
                detected_people = detect_face(image_path)

            surface, _surface_buf = _create_cairo_surface_from_image(image_path)
            ctx = cairo.Context(surface)
            # 预处理: 替换speaker中的尖括号部分（使用speaker格式）
            speakers = item['speaker']
            
            # 初始化障碍物列表
            existing_obstacles = [person['bbox'] for person in detected_people]
             
            # 汇总所有候选说话人：以 text_type 为准，只从 dialogue/monologue 中收集
            # 即便 speaker == narrator，只要 text_type 是 dialogue/monologue，也按对话处理
            all_speakers = list(set(
                speaker
                for speaker, tt in zip(speakers, text_types)
                if tt in ["dialogue", "monologue"] and speaker
            ))
            
            # 为所有检测到的人脸计算与所有候选说话人的相似度
            if needs_face_detection and detected_people and all_speakers:
                get_all_face_speaker_similarities(detected_people, all_speakers)

            # 处理每一条文本（narration 优先）
            texts = item['text']
            text_types = item['text_type']
            priority = {"narration": 0}
            order = sorted(
                range(len(texts)),
                key=lambda i: (priority.get(text_types[i], 1), i)
            )

            for text_idx in order:
                text = texts[text_idx]
                speaker = speakers[text_idx]
                text_type = text_types[text_idx]
                # 预处理: 替换speaker中的尖括号部分（使用speaker格式）

                # 决定对话框大小
                text_length = len(text)
                if text_length < 8:
                    box_w, box_h = 300, 75
                elif text_length < 25:
                    box_w, box_h = 350, 150
                elif text_length < 50:
                    box_w, box_h = 400, 200
                else:
                    box_w, box_h = 500, 250
                
                # 针对 Sound Effect 调整形状
                if text_type in ["sound effect", "sound_effect"]:
                    box_w, box_h = min(300, 50+text_length * 35), 200

                dialogue_box_size = (box_w, box_h)
                
                best_dialogue_box = None
                arrow_position = None
                speaker_found_in_image = False

                # 分类处理位置
                if text_type in ["sound effect", "sound_effect"]:
                    # 随机分布在两侧
                    best_dialogue_box, center_pos = get_random_se_position(
                        (img_w, img_h), 
                        dialogue_box_size, 
                        existing_obstacles
                    )
                    arrow_position = center_pos

                elif text_type == "narration":
                    # 旁白固定在底部
                    margin = 20
                    h_y = int(img_h * 0.85)
                    best_dialogue_box = (margin, h_y, img_w - margin, img_h - margin)
                    arrow_position = (img_w/2, img_h/2)

                else:  # dialogue 或 monologue
                    if detected_people:
                        # 检查该说话人是否在屏幕中出现
                        # 通过检查该说话人是否是任何人脸的最高相似度说话人
                        speaker_best_match_person = None
                        max_similarity = -1
                        
                        for person in detected_people:
                            speaker_similarities = person.get('speaker_similarities', {})
                            if speaker in speaker_similarities and speaker_similarities:
                                sim = speaker_similarities[speaker]
                                # 检查这个说话人是否是该人脸的最高相似度
                                best_speaker_for_person = max(
                                    speaker_similarities.items(),
                                    key=lambda x: x[1]
                                )[0]
                                
                                if best_speaker_for_person == speaker and sim > max_similarity:
                                    max_similarity = sim
                                    speaker_best_match_person = person
                                    speaker_found_in_image = True
                        
                        if speaker_found_in_image and speaker_best_match_person:
                            # 说话人在屏幕中出现，使用原有逻辑
                            speaker_bbox = speaker_best_match_person['bbox']
                            best_dialogue_box, arrow_position = find_best_dialogue_box_position(
                                speaker_bbox,
                                existing_obstacles, 
                                dialogue_box_size,
                                (img_w, img_h)
                            )
                        else:
                            # 说话人不在屏幕中，只使用四角候选位置
                            best_dialogue_box, arrow_position = find_best_dialogue_box_position(
                                (0, 0, 0, 0),  # 虚拟bbox，不会使用
                                existing_obstacles, 
                                dialogue_box_size,
                                (img_w, img_h),
                                only_corners=True
                            )
                    else:
                        # 没检测到人脸时的兜底
                        best_dialogue_box = (20, 20, 20+box_w, 20+box_h)
                        arrow_position = (box_w/2, box_h + 50)

                # 绘制
                if best_dialogue_box:
                    existing_obstacles.append(best_dialogue_box)

                ctx.select_font_face("HYWenHei 55W", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
                ctx.set_font_size(36)
                
                if text_type == "dialogue":
                    draw_speech_bubble(ctx, "rect", best_dialogue_box, arrow_position)
                    draw_text(ctx, best_dialogue_box, 40, text)
                elif text_type == "monologue":
                    draw_speech_bubble(ctx, "wavy_rect", best_dialogue_box, arrow_position)
                    draw_text(ctx, best_dialogue_box, 40, text)
                elif text_type == "narration":
                    draw_rect(ctx, best_dialogue_box, fill=True)
                    draw_text(ctx, best_dialogue_box, 40, text)
                elif text_type in ["sound effect", "sound_effect"]:
                    draw_speech_bubble(ctx, "ellipse", best_dialogue_box, arrow_position)
                    draw_text(ctx, best_dialogue_box, 40, text)
            
            # # 绘制调试信息
            # # 1. 在左上角列出所有候选说话人
            # ctx.set_source_rgb(0, 0, 0)  # 黑色
            # ctx.select_font_face("YouYuan", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            # ctx.set_font_size(18)
            
            # y_offset = 20
            # for speaker_name in all_speakers:
            #     ctx.move_to(10, y_offset)
            #     ctx.show_text(f"Speaker: {speaker_name}")
            #     y_offset += 25
            
            # # 2. 为每个检测到的人脸绘制框和相似度信息
            # for person in detected_people:
            #     x1, y1, x2, y2 = person['bbox']
                
            #     # 绘制人脸框（绿色）
            #     ctx.set_source_rgb(0, 1, 0)  # 绿色
            #     ctx.set_line_width(2)
            #     ctx.rectangle(x1, y1, x2 - x1, y2 - y1)
            #     ctx.stroke()
                
            #     # 在人脸框上方绘制相似度信息
            #     ctx.set_source_rgb(0, 0, 0)  # 黑色
            #     y_text = y1 - 10
            #     for speaker_name in all_speakers:
            #         sim = person['speaker_similarities'].get(speaker_name, 0)
            #         ctx.move_to(x1, y_text)
            #         ctx.show_text(f"{speaker_name}: {sim:.1f}")
            #         y_text -= 25

            # 保存输出
            output_filename = os.path.basename(image_path_rel)
            stem, _ = os.path.splitext(output_filename)
            output_filename = f"{stem}.jpg"
            output_save_path = str(output_dir / output_filename)

            _save_cairo_surface_as_jpg(surface, output_save_path)
        
        return f"✓ ID {item.get('id')} 处理完成"
    except Exception as e:
        return f"✗ ID {item.get('id')} 处理失败: {str(e)}"

def add_dialog_and_text(book_id: str = "000", chapter: str = None):
    """
    为图像添加对话框和文本
    
    Args:
        book_id: 书籍ID (默认: 000)
        chapter: 特定章节ID (可选)
    """
    script_dir = f"script/{book_id}"

    if not os.path.exists(script_dir):
        print("book_id目录不存在")
        exit()
    if chapter:  
        # 只处理指定chapter
        json_files = [f"{chapter}.json"]
    else:
        # 处理目录下所有json
        json_files = [
            f for f in os.listdir(script_dir)
            if f.endswith(".json")
        ]

        if not json_files:
            print("目录下没有json文件")
            exit()

    for json_file in json_files:

        chapter = os.path.splitext(json_file)[0]
        json_path = os.path.join(script_dir, json_file)

        print("\n" + "="*60)
        print(f"开始处理章节: {chapter}")
        print("="*60)

        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # 创建输出目录
        output_base_dir = f'with_text/{book_id}/{chapter}'
        os.makedirs(output_base_dir, exist_ok=True)

        output_json_path = os.path.join(output_base_dir, f'{chapter}.json')

        print(f"开始处理 {len(data)} 个item，使用4个线程...")

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(process_item, item, book_id, chapter, output_base_dir)
                for item in data
            ]

            results = []
            for future in tqdm(futures, total=len(data),
                               desc=f"{chapter} 处理进度",
                               unit="item", ncols=80):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    results.append(f"错误: {str(e)}")
        print("\n" + "-"*60)
        print(f"{chapter} 处理结果汇总:")
        print("-"*60)
        for result in results:
            print(result)

        # 保存json
        with open(output_json_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print(f"\n已保存json到 {output_json_path}")

    print("\n全部处理完成！")


if __name__ == "__main__":

    book_id = "000"
    chapter = "原神 copy 23"   # 设置为None表示处理所有章节

    add_dialog_and_text(book_id=book_id, chapter=chapter)
