import pandas as pd
import matplotlib.pyplot as plt
import seaborn as sns

# 设置中文字体（根据系统环境调整，如黑体 SimHei）
plt.rcParams['font.sans-serif'] = ['SimHei'] 
plt.rcParams['axes.unicode_minus'] = False


def _to_numeric_rate(series):
    """将可能带百分号的列安全转换为数值（0~1）。"""
    if pd.api.types.is_numeric_dtype(series):
        return series
    s = series.astype(str).str.strip()
    is_percent = s.str.contains('%', na=False)
    s = s.str.replace('%', '', regex=False)
    numeric = pd.to_numeric(s, errors='coerce')
    return numeric.where(~is_percent, numeric / 100)


def _build_discrete_summary(df_all, df_calc, var, low_statuses):
    """
    统计离散变量：平均播放、中位播放（基于计算样本）+ 低俗比例（基于全量样本）。
    低俗比例定义：审核状态为自见或需优化的作品占比。
    """
    total_cnt = df_all.groupby(var).size().rename('作品数')
    low_cnt = (
        df_all[df_all['审核状态'].isin(low_statuses)]
        .groupby(var)
        .size()
        .rename('低俗作品数')
    )
    avg_play = df_calc.groupby(var)['播放量'].mean().rename('平均播放量')
    median_play = df_calc.groupby(var)['播放量'].median().rename('中位播放量')

    summary = pd.concat([total_cnt, avg_play, median_play, low_cnt], axis=1).fillna(0)
    summary['低俗比例'] = summary['低俗作品数'] / summary['作品数']
    summary = summary.drop(columns=['低俗作品数'])
    return summary.sort_values(by='平均播放量', ascending=False)


def _map_publish_period(dt_series):
    """将发布时间映射为 凌晨/上午/下午/晚上。"""
    hours = pd.to_datetime(dt_series, errors='coerce').dt.hour

    def _to_period(h):
        if pd.isna(h):
            return '未知'
        h = int(h)
        if 0 <= h < 6:
            return '凌晨'
        if 6 <= h < 12:
            return '上午'
        if 12 <= h < 18:
            return '下午'
        return '晚上'

    return hours.apply(_to_period)


def _explode_comma_separated_column(df_all, df_calc, column_name):
    """将形如“角色A,角色B”的单元格拆分并展开为多行。"""
    if column_name not in df_all.columns or column_name not in df_calc.columns:
        return df_all, df_calc

    df_all_expanded = df_all.copy()
    df_calc_expanded = df_calc.copy()

    df_all_expanded[column_name] = (
        df_all_expanded[column_name]
        .fillna('')
        .astype(str)
        .str.split(',')
    )
    df_calc_expanded[column_name] = (
        df_calc_expanded[column_name]
        .fillna('')
        .astype(str)
        .str.split(',')
    )

    df_all_expanded = df_all_expanded.explode(column_name)
    df_calc_expanded = df_calc_expanded.explode(column_name)

    df_all_expanded[column_name] = df_all_expanded[column_name].astype(str).str.strip()
    df_calc_expanded[column_name] = df_calc_expanded[column_name].astype(str).str.strip()

    df_all_expanded = df_all_expanded[df_all_expanded[column_name].notna() & (df_all_expanded[column_name] != '')]
    df_calc_expanded = df_calc_expanded[df_calc_expanded[column_name].notna() & (df_calc_expanded[column_name] != '')]

    return df_all_expanded, df_calc_expanded


def _summarize_rare_categories_as_other(summary, min_count=2):
    """将作品数小于 min_count 的类别合并到“其他”。"""
    if summary.empty:
        return summary

    major = summary[summary['作品数'] >= min_count].copy()
    rare = summary[summary['作品数'] < min_count].copy()

    if rare.empty:
        return major

    other_count = int(rare['作品数'].sum())
    other_avg_play = (rare['平均播放量'] * rare['作品数']).sum() / other_count if other_count else pd.NA
    other_low_ratio = (rare['低俗比例'] * rare['作品数']).sum() / other_count if other_count else pd.NA

    other_row = pd.DataFrame(
        {
            '作品数': [other_count],
            '平均播放量': [other_avg_play],
            '低俗比例': [other_low_ratio],
        },
        index=['其他']
    )

    result = pd.concat([major, other_row], axis=0)
    return result.sort_values(by='平均播放量', ascending=False)


def analyze_tiktok_data(file_path):
    # 1. 加载数据
    df = pd.read_excel(file_path)

    low_statuses = ['自见', '需优化']
    calc_statuses = ['公开', '需优化']

    # 参与计算样本：公开 + 需优化（剔除自见）
    df_calc = df[df['审核状态'].isin(calc_statuses)].copy()

    # 全局低俗比例（基于总作品）
    low_ratio_total = df['审核状态'].isin(low_statuses).mean()
    print(f"全局低俗比例（自见+需优化/总作品）: {low_ratio_total:.2%}")

    # 转换百分比数据为浮点数（如果Excel读入时不是小数）
    rate_cols = ['完播率', '5s完播率', '封面点击率', '2s跳出率']
    for col in rate_cols:
        if col in df.columns:
            df[col] = _to_numeric_rate(df[col])
        if col in df_calc.columns:
            df_calc[col] = _to_numeric_rate(df_calc[col])
    
    # 计算一些核心转化率指标
    df_calc['点赞率'] = (df_calc['点赞量'] / df_calc['播放量']).replace([float('inf'), -float('inf')], pd.NA)
    df_calc['收藏率'] = (df_calc['收藏量'] / df_calc['播放量']).replace([float('inf'), -float('inf')], pd.NA)

    # --- 功能一：离散数据分析 ---
    # 题材类型更名为题材
    if '题材类型' in df.columns and '题材' not in df.columns:
        df = df.rename(columns={'题材类型': '题材'})
        df_calc = df_calc.rename(columns={'题材类型': '题材'})

    # 发布时间离散化：上午、下午、晚上、凌晨
    if '发布时间' in df.columns:
        df['发布时间区间'] = _map_publish_period(df['发布时间'])
    if '发布时间' in df_calc.columns:
        df_calc['发布时间区间'] = _map_publish_period(df_calc['发布时间'])

    # 主要角色：按英文逗号拆分后分别统计
    df_role_all, df_role_calc = _explode_comma_separated_column(df, df_calc, '主要角色')

    discrete_vars = ['题材', '角色来源', '是否为系列作品', '是否包含新角色', '剧情背景设定', '发布时间区间', '主要角色']

    print("### 离散维度表现分析 ###")
    for var in discrete_vars:
        if var not in df.columns:
            print(f"\n[{var}] 列不存在，跳过。")
            continue

        if var == '主要角色':
            summary = _build_discrete_summary(df_role_all, df_role_calc, var, low_statuses)
            summary = _summarize_rare_categories_as_other(summary, min_count=2)
            print(f"\n基于 [{var}] 的数据汇总:")
            print(summary[['作品数', '平均播放量', '中位播放量', '低俗比例']].to_string())
        else:
            summary = _build_discrete_summary(df, df_calc, var, low_statuses)
            print(f"\n基于 [{var}] 的数据汇总:")
            print(summary[['作品数', '平均播放量', '中位播放量', '低俗比例']])
        
        # 平均播放量和中位数分别作图
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 5))
        
        summary['平均播放量'].plot(kind='bar', ax=ax1, title=f'不同{var}的平均播放量', color='steelblue')
        ax1.set_ylabel('平均播放量')
        
        summary['中位播放量'].plot(kind='bar', ax=ax2, title=f'不同{var}的中位播放量', color='orange')
        ax2.set_ylabel('中位播放量')
        
        plt.tight_layout()
        plt.show()

    # --- 功能二：原文作者分析 ---
    print("\n### 原文作者分析（>1单独展示，=1并入其他） ###")
    author_col = '原文作者'
    if author_col in df.columns:
        author_summary = _build_discrete_summary(df, df_calc, author_col, low_statuses)

        multi_authors = author_summary[author_summary['作品数'] > 1].copy()
        single_authors = author_summary[author_summary['作品数'] == 1].copy()

        if not multi_authors.empty:
            print("\n作品数 > 1 的作者:")
            print(multi_authors[['作品数', '平均播放量', '中位播放量', '低俗比例']])
        else:
            print("\n没有作品数 > 1 的作者。")

        if not single_authors.empty:
            other_authors = single_authors.index.tolist()
            other_mask_all = df[author_col].isin(other_authors)
            other_mask_calc = df_calc[author_col].isin(other_authors)

            other_author_count = int(other_mask_all.sum())
            other_avg_play = df_calc.loc[other_mask_calc, '播放量'].mean()
            other_median_play = df_calc.loc[other_mask_calc, '播放量'].median()
            other_low_ratio = df.loc[other_mask_all, '审核状态'].isin(low_statuses).mean()
            other_row = pd.DataFrame(
                {
                    '作品数': [other_author_count],
                    '平均播放量': [other_avg_play],
                    '中位播放量': [other_median_play],
                    '低俗比例': [other_low_ratio],
                },
                index=['其他']
            )
            print("\n作者=1 的合并结果（其他）:")
            print(other_row.to_string())

        author_plot_summary = multi_authors.copy()
        if not single_authors.empty:
            author_plot_summary = pd.concat([author_plot_summary, other_row], axis=0)

        if not author_plot_summary.empty:
            fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 6))
            author_plot_summary['平均播放量'].plot(kind='bar', ax=ax1, title='原文作者平均播放量')
            ax1.set_ylabel('平均播放量')

            author_plot_summary['中位播放量'].plot(kind='bar', ax=ax2, title='原文作者中位播放量', color='orange')
            ax2.set_ylabel('中位播放量')

            plt.tight_layout()
            plt.show()
    else:
        print("[原文作者] 列不存在，跳过作者分析。")

    # --- 功能三：连续数据相关性分析 ---
    # 定义需要分析的数值列
    continuous_vars = ['平均播放时长', '粉丝增量', '完播率', '2s跳出率', '点赞率']
    
    print("\n### 连续变量与播放量的相关程度 (Pearson) ###")
    available_continuous = [c for c in continuous_vars if c in df_calc.columns]
    correlations = df_calc[available_continuous + ['播放量']].corr()['播放量'].sort_values(ascending=False)
    print(correlations)

    # 绘图：展示趋势（不再分析长度）
    trend_vars = [
        ('平均播放时长', '平均播放时长与播放量的变化趋势', 'green'),
        ('粉丝增量', '粉丝增量与播放量的变化趋势', 'purple'),
    ]
    available_trend_vars = [item for item in trend_vars if item[0] in df_calc.columns]

    if available_trend_vars:
        fig, axes = plt.subplots(1, len(available_trend_vars), figsize=(8 * len(available_trend_vars), 6))
        if len(available_trend_vars) == 1:
            axes = [axes]

        for ax, (x_col, title, color) in zip(axes, available_trend_vars):
            sns.regplot(x=x_col, y='播放量', data=df_calc, ax=ax, lowess=True, line_kws={'color': color})
            ax.set_title(title)

        plt.tight_layout()
        plt.show()

# 使用示例
analyze_tiktok_data(r'z其他\抖音作品数据-三月.xlsx')