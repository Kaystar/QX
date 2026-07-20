#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cron: 0 8 * * *
new Env('🧩 视频版本检测');
"""

"""
=========================================
          STRM多版本重复检测脚本
=========================================
使用说明：
1. 本脚本用于检测飞牛/NAS 媒体目录下 strm 文件中，标准番号与特定后缀版本共存的重复情况。
2. 重复定义：
   - 存在原档 "ABCD-123" 且同时存在指定的后缀版本（如 "ABCD-123-C"）时 -> 算重复。
   - 仅存在多后缀版本，但原档不存在 -> 不算重复。
3. 排序机制：
   - 最终输出的报告会严格按照番号字母顺序（A-Z）进行升序排列。
   - 首字母相同对比次字母，以此类推，且排序时不区分大小写。
4. 白名单过滤（外部文件形式）：
   - 脚本会自动读取同级目录下的 `strm_dup_exclude.list` 文件。
   - 你可以直接在该文件中每行填写一个需要豁免检测的番号，支持用 # 写注释。
   - 列表中番号不区分大小写，写在里面的番号将彻底不参与检测。

环境变量配置：
- MEDIA_DIR      : 必须配置。需要扫描的飞牛本地媒体目录绝对路径，多个目录用英文逗号(,)分隔。
                   例如：/vol1/media/Movie 或 /vol1/media/Movie1,/vol1/media/Movie2
- DETECT_VERSIONS: 可选配置。指定要参与和原档进行比对检测的后缀版本，多个用英文逗号(,)分隔。不区分大小写。
                   - 填具体后缀（推荐）：例如 `-C,-4K`，则只有 `-C` 和 `-4K` 版本与原档共存时才会报重复。像 `-AI`、`-CD1` 等版本会被直接忽略。
                   - 填 `ALL` 或不配置（默认）：所有带后缀的版本都参与检测。

青龙通知与结果：
- 消息轰炸优化：Telegram 通知中【仅发送总结数据】，不再发送详细列表。
- 详细结果查看：具体的重复番号列表将自动写入脚本同级目录下的 `🧩视频版本检测结果.txt` 文件中，同时完整打印到控制台日志。
=========================================
"""

import os
import re
import sys

# 获取脚本所在的当前目录路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# 排除白名单文件路径（已改为英文 strm_dup_exclude.list）
EXCLUDE_FILE_PATH = os.path.join(CURRENT_DIR, "strm_dup_exclude.list")


# ==========================================
# 移植自高效 NFO 脚本的通知网络网关加载逻辑
# ==========================================
def send_notify(title, content):
    for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts', os.path.dirname(__file__)]:
        if os.path.exists(os.path.join(p, 'sendNotify.py')) and p not in sys.path: 
            sys.path.append(p)
    try:
        from sendNotify import send; send(title, content)
    except Exception as e:
        try:
            from sendNotify import sendNotify; sendNotify(title, content)
        except Exception: 
            print(f"🎉 提示：未检测到青龙内置通知模块或发送失败: {e}")
            print(f"\n【控制台备份显示 - {title}】\n{content}")


def log(message):
    """打印运行日志"""
    print(f"[日志] {message}")


def load_exclude_list():
    """从外部 list 文件中读取排除白名单，若文件不存在则初始化一个带有示例的文件"""
    exclude_set = set()
    
    # 如果文件不存在，自动创建并写入默认说明和示例
    if not os.path.exists(EXCLUDE_FILE_PATH):
        try:
            with open(EXCLUDE_FILE_PATH, "w", encoding="utf-8") as f:
                f.write("# ==================================================\n")
                f.write("# strm_dup_exclude.list (视频版本检测排除白名单)\n")
                f.write("# 使用说明：\n")
                f.write("# 1. 每行写一个需要忽略检测的番号，例如：EXMP-001\n")
                f.write("# 2. 番号不区分大小写\n")
                f.write("# 3. 行首使用 # 号代表注释，该行将被脚本忽略\n")
                f.write("# ==================================================\n")
                f.write("EXMP-001\n")
                f.write("TEST-999\n")
            log(f"📝 未检测到排除白名单文件，已自动初始化创建: {EXCLUDE_FILE_PATH}")
        except Exception as e:
            log(f"❌ 自动创建排除白名单文件失败: {e}")
            return exclude_set

    # 开始读取文件内容
    try:
        with open(EXCLUDE_FILE_PATH, "r", encoding="utf-8") as f:
            for line in f:
                clean_line = line.strip()
                # 略过空行和以 # 开头的注释行
                if not clean_line or clean_line.startswith("#"):
                    continue
                exclude_set.add(clean_line.lower())
        log(f"🛡️ 成功从 {os.path.basename(EXCLUDE_FILE_PATH)} 加载了 {len(exclude_set)} 个豁免检测番号")
    except Exception as e:
        log(f"❌ 读取排除白名单文件失败: {e}")
        
    return exclude_set


def extract_base_and_suffix(filename):
    """
    解析文件名/番号，拆分为(基础番号, 后缀)
    匹配常见的后缀连接符：-, _, 空格 加上常见后缀
    """
    name_without_ext, _ = os.path.splitext(filename)
    
    # 正则：匹配 [番号] + [分隔符(横杠/下划线/空格)] + [字母/数字后缀]
    # 例如：ABCD-123-C -> 基础: ABCD-123, 后缀: -C
    pattern = re.compile(r'^([a-zA-Z0-9]+-[0-9]+)([-_\s][a-zA-Z0-9]+)+$')
    match = pattern.match(name_without_ext)
    
    if match:
        base_id = match.group(1)
        suffix = name_without_ext[len(base_id):]
        return base_id, suffix
    else:
        return name_without_ext, ""


def get_version_info(suffix):
    """
    根据后缀，返回对应的 (等大彩色圆形Emoji, 翻译名称)
    """
    clean_suffix = suffix.strip().upper()
    if clean_suffix in ["-C", "_C", " C"]:
        return "🟠", "中字"
    elif clean_suffix in ["-4K", "_4K", " 4K"]:
        return "🟢", "超清"
    
    # 其它后缀去除前面的连接符，大写后输出，统一使用黄色圆形
    clean_tag = re.sub(r'^[-_\s]+', '', suffix).strip().upper()
    return "🟡", clean_tag


def scan_and_find_duplicates(media_dirs, detect_versions):
    """扫描目录，找出重复版本（从外部 list 文件获取白名单过滤）"""
    db = {}
    
    # 动态从外部 List 文件获取排除名单
    exclude_set = load_exclude_list()
    
    # 解析过滤后缀列表
    filter_enabled = False
    allowed_suffixes = []
    if detect_versions and detect_versions.strip().upper() != "ALL":
        filter_enabled = True
        allowed_suffixes = [v.strip().upper() for v in detect_versions.split(",") if v.strip()]
        log(f"⚙️ 过滤规则已启用，仅检测以下后缀版本: {allowed_suffixes}")
    else:
        log("⚙️ 过滤规则未启用（或为ALL），所有后缀版本均参与检测。")

    for directory in media_dirs:
        directory = directory.strip()
        if not directory:
            continue
        
        if not os.path.exists(directory):
            log(f"❌ 目录不存在，跳过扫描: {directory}")
            continue
            
        log(f"🔍 开始扫描目录: {directory}")
        
        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith('.strm'):
                    base_id, suffix = extract_base_and_suffix(file)
                    
                    # 判断基础番号是否在排除白名单内
                    if base_id.lower() in exclude_set:
                        continue
                    
                    if base_id not in db:
                        db[base_id] = {'base_file': None, 'sub_versions': []}
                    
                    if not suffix:
                        db[base_id]['base_file'] = file
                    else:
                        if filter_enabled and suffix.strip().upper() not in allowed_suffixes:
                            continue
                        db[base_id]['sub_versions'].append((file, suffix))

    duplicates = {}
    for base_id, info in db.items():
        # 判定条件：原档存在，且至少存在一个被允许参与检测的后缀版本
        if info['base_file'] and info['sub_versions']:
            duplicates[base_id] = {
                'original': info['base_file'],
                'others': info['sub_versions']
            }
            
    return duplicates


def write_and_log_details(duplicates):
    """将重复详情按番号字母顺序排序，写入文本文件，并同步打印到运行日志中"""
    result_file_path = os.path.join(CURRENT_DIR, "🧩视频版本检测结果.txt")
    
    # 使用 sorted 进行字典序升序排列
    sorted_duplicates = sorted(duplicates.items(), key=lambda x: x[0].lower())
    
    report_lines = []
    report_lines.append("==================================================")
    report_lines.append("               🧩 视频版本检测详细报告")
    report_lines.append("==================================================")
    report_lines.append(f"生成时间: {os.popen('date').read().strip()}\n")
    
    for idx, (base_id, files) in enumerate(sorted_duplicates, 1):
        report_lines.append(f"{idx}. 番号: {base_id}")
        report_lines.append(f"   🔵 原档: {files['original']}")
        
        # 遍历并输出具体的冲突后缀
        for other_file, suffix in files['others']:
            emoji, tag_name = get_version_info(suffix)
            report_lines.append(f"   {emoji} {tag_name}: {other_file}")
        report_lines.append("-" * 40)
        
    full_report = "\n".join(report_lines)
    
    # 1. 打印在控制台日志里
    print("\n" + "="*20 + " 📝 冲突明细报告 " + "="*20)
    print(full_report)
    print("="*55 + "\n")
    
    # 2. 写入本地文件
    try:
        with open(result_file_path, "w", encoding="utf-8") as f:
            f.write(full_report + "\n")
        log(f"💾 详细重复列表已成功写入文件: {result_file_path}")
    except Exception as e:
        log(f"❌ 写入结果文件失败: {e}")


def main():
    log("🚀 脚本启动，准备执行重复 STMR 检测...")
    
    media_dir_env = os.environ.get("MEDIA_DIR")
    if not media_dir_env:
        log("❌ 错误: 未配置环境变量 `MEDIA_DIR`。请在青龙环境变量中添加该变量并填写飞牛 strm 目录路径。")
        send_notify("🧩视频版本检测失败", "未配置环境变量 `MEDIA_DIR`，脚本已中止。")
        sys.exit(1)
        
    # 获取需要检测的版本后缀配置
    detect_versions = os.environ.get("DETECT_VERSIONS", "ALL")
    
    media_dirs = media_dir_env.split(',')
    log(f"📋 待扫描路径列表: {media_dirs}")
    
    duplicates = scan_and_find_duplicates(media_dirs, detect_versions)
    
    if not duplicates:
        summary_msg = "✅ 扫描完成！未发现【原档与指定版本共存】的重复 strm 文件。"
        log(summary_msg)
        send_notify("🧩视频版本检测完成", summary_msg)
        sys.exit(0)
        
    total_count = len(duplicates)
    detail_count = sum(len(v['others']) + 1 for v in duplicates.values())
    
    # 整理结果并【写入文件 + 打印控制台日志】
    write_and_log_details(duplicates)
    
    # 组装精简版 Telegram 通知内容
    summary_msg = (
        f"📊 *检测结果总结：*\n"
        f"- 共发现 **{total_count}** 组存在冲突的番号\n"
        f"- 涉及重复 strm 文件共 **{detail_count}** 个。\n\n"
        f"📝 *温馨提示：*\n"
        f"详细的重复名单已写入脚本目录下的 `🧩视频版本检测结果.txt` 文件中，并已按字母 A-Z 顺序排列完毕，请前往查看。"
    )
    
    # 发送单条总结通知
    log("✉️ 正在发送总结通知...")
    send_notify("🧩视频版本检测报告", summary_msg)
    log("🏁 任务运行结束。")


if __name__ == '__main__':
    main()
