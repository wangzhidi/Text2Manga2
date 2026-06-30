import os
import re

def process_txt_file(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        # 删除行末的数字（包括前面的空格）
        new_line = re.sub(r'\s*\d+\s*$', '', line.rstrip('\n'))
        new_lines.append(new_line + '\n')

    # 覆盖写回
    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)


def process_folder(folder_path):
    for root, dirs, files in os.walk(folder_path):
        for file in files:
            if file.endswith('.txt'):
                file_path = os.path.join(root, file)
                print(f'处理文件: {file_path}')
                process_txt_file(file_path)


if __name__ == "__main__":
    folder = r"books\魔法少女独断万古"
    process_folder(folder)
    print("处理完成！")