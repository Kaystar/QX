#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cron: 0 8 * * *
new Env('🔢视频版本检测');
"""

"""
=========================================
          STRM多版本重复检测脚本
=========================================
使用说明：
1. 本脚本用于检测飞牛/NAS 媒体目录下 strm 文件中，标准番号与带后缀多版本番号共存的重复情况。
2. 重复定义：
   - 存在原版 "ABCD-123" 且同时存在 "ABCD-123-C" 或 "ABCD-123-4K" 时 -> 算重复。
   - 仅存在 "ABCD-123-C" 和 "ABCD-123-4K"（原版不存在） -> 不算重复。

环境变量配置：
- MEDIA_DIR: 必须配置。需要扫描的飞牛本地媒体目录绝对路径，多个目录用英文逗号(,)分隔。
             例如：/vol1/media/Movie 或 /vol1/media/Movie1,/vol1/media/Movie2

青龙通知：
- 脚本会自动多路径寻找并使用青龙的 `sendNotify.py` 模块。
- 考虑到 Telegram 的消息长度限制（约 4096 字符），通知内容如果过长，脚本会自动拆分成多条依次发送。
=========================================
"""

import os
import re
import sys

# ==========================================
# 精准锁定并导入青龙 scripts 目录下的通知模块
# ==========================================
HAS_NOTIFY = False

possible_paths = [
    '/ql/data/scripts', 
    '/ql/scripts',
    os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts')),
    os.path.abspath(os.path.join(os.path.dirname(__file__), '../')),
    os.path.dirname(__file__)
]

for ql_path in possible_paths:
    if os.path.exists(os.path.join(ql_path, 'sendNotify.py')):
        sys.path.append(ql_path)
        try:
            from sendNotify import send_notification
            HAS_NOTIFY = True
            print(f"[日志] 🎯 成功对接青龙内置通知模块，路径: {ql_path}")
            break
        except Exception as e:
            print(f"[日志] 尝试从 {ql_path} 导入失败: {e}")

if not HAS_NOTIFY:
    print("⚠️ 提示：未成功加载 scripts 目录下的 sendNotify.py 模块，通知将仅在控制台打印。")


def log(message):
    """打印详细运行日志"""
    print(f"[日志] {message}")


def extract_base_and_suffix(filename):
    """
    解析文件名/番号，拆分为(基础番号, 后缀)
    匹配常见的后缀连接符：-, _, 空格 加上常见后缀如 C, 4k, 1080p, 60fps, CD1, UNCUT 等
    """
    # 移除文件后缀名
    name_without_ext, _ = os.path.splitext(filename)
    
    # 正则：匹配 [番号] + [分隔符(横杠/下划线/空格)] + [字母/数字后缀]
    # 例如：ABCD-123-C -> 基础: ABCD-123, 后缀: C
    pattern = re.compile(r'^([a-zA-Z0-9]+-[0-9]+)([-_\s][a-zA-Z0-9]+)+$')
    match = pattern.match(name_without_ext)
    
    if match:
        base_id = match.group(1)
        suffix = name_without_ext[len(base_id):]
        return base_id, suffix
    else:
        return name_without_ext, ""


def scan_and_find_duplicates(media_dirs):
    """扫描目录，找出重复版本"""
    db = {}
    
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
                    
                    if base_id not in db:
                        db[base_id] = {'base_file': None, 'sub_versions': []}
                    
                    if not suffix:
                        db[base_id]['base_file'] = file
                    else:
                        db[base_id]['sub_versions'].append(file)

    duplicates = {}
    for base_id, info in db.items():
        if info['base_file'] and info['sub_versions']:
            duplicates[base_id] = {
                'original': info['base_file'],
                'others': info['sub_versions']
            }
            
    return duplicates


def send_tg_notification(summary, detail_list):
    """分包发送 Telegram 通知"""
    if not HAS_NOTIFY:
        log("📢 [未发送通知] 因未找到 sendNotify.py 模块，结果直接在下方展示：")
        print(summary)
        print("\n".join(detail_list))
        return

    MAX_CHAR = 3000
    current_message = f"🔔 *🔢视频版本检测报告*\n\n{summary}\n\n"
    current_message += "⚠️ *重复详情列表：*\n"
    
    messages_to_send = []
    
    for detail in detail_list:
        if len(current_message) + len(detail) > MAX_CHAR:
            messages_to_send.append(current_message)
            current_message = "🔄 *🔢视频版本检测报告（续）：*\n\n"
        
        current_message += detail + "\n"
    
    if current_message:
        messages_to_send.append(current_message)
        
    for idx, msg in enumerate(messages_to_send):
        title = f"🔢视频版本检测 ({idx + 1}/{len(messages_to_send)})"
        log(f"✉️ 正在发送第 {idx + 1} 部分通知...")
        send_notification(title, msg)


def main():
    log("🚀 脚本启动，准备执行重复 STMR 检测...")
    
    media_dir_env = os.environ.get("MEDIA_DIR")
    if not media_dir_env:
        log("❌ 错误: 未配置环境变量 `MEDIA_DIR`。请在青龙环境变量中添加该变量并填写飞牛 strm 目录路径。")
        if HAS_NOTIFY:
            send_notification("🔢视频版本检测失败", "未配置环境变量 `MEDIA_DIR`，脚本已中止。")
        sys.exit(1)
        
    media_dirs = media_dir_env.split(',')
    log(f"📋 待扫描路径列表: {media_dirs}")
    
    duplicates = scan_and_find_duplicates(media_dirs)
    
    if not duplicates:
        summary_msg = "✅ 扫描完成！未发现【原版与多版本共存】的重复 strm 文件。"
        log(summary_msg)
        if HAS_NOTIFY:
            send_notification("🔢视频版本检测完成", summary_msg)
        sys.exit(0)
        
    total_count = len(duplicates)
    detail_count = sum(len(v['others']) + 1 for v in duplicates.values())
    
    summary_msg = f"📊 *检测结果总结：*\n- 共发现 **{total_count}** 组存在冲突的番号\n- 涉及重复 strm 文件共 **{detail_count}** 个。"
    log(f"发现 {total_count} 组重复，准备输出详情...")
    
    detail_list = []
    for idx, (base_id, files) in enumerate(duplicates.items(), 1):
        detail = f"{idx}. 📇 番号: `{base_id}`\n"
        detail += f"   🔹 原版: `{files['original']}`\n"
        detail += f"   🔸 冲突版本:\n"
        for other in files['others']:
            detail += f"      - `{other}`\n"
        detail_list.append(detail)
        print(f"[{base_id}] 重复: 原版[{files['original']}] <-> 冲突{files['others']}")

    send_tg_notification(summary_msg, detail_list)
    log("🏁 任务运行结束。")


if __name__ == '__main__':
    main()
