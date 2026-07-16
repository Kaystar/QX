#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
cron: 0 8 * * *
new Env('STRM多版本重复检测');
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
- 脚本会自动导入并使用青龙的 `sendNotify.py` 模块。
- 考虑到 Telegram 的消息长度限制（约 4096 字符），通知内容如果过长，脚本会自动拆分成多条依次发送。
=========================================
"""

import os
import re
import sys

# 尝试导入青龙通知模块
try:
    # 确保能找到 scripts 目录下的 sendNotify
    sys.path.append('/ql/data/scripts')
    sys.path.append('/ql/scripts')
    from sendNotify import send_notification
    HAS_NOTIFY = True
except ImportError:
    HAS_NOTIFY = False
    print("⚠️ 提示：未检测到青龙 sendNotify 模块，通知将仅在控制台打印。")


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
    # 针对 115 strm 命名习惯，通常番号后会带 -C, -4k, -CD1, _uncut 等
    pattern = re.compile(r'^([a-zA-Z0-9]+-[0-9]+)([-_\s][a-zA-Z0-9]+)+$')
    match = pattern.match(name_without_ext)
    
    if match:
        base_id = match.group(1)
        # 获取除了基础番号外剩余的后缀部分
        suffix = name_without_ext[len(base_id):]
        return base_id, suffix
    else:
        # 如果不符合上述结构，认为它本身就是基础番号
        return name_without_ext, ""


def scan_and_find_duplicates(media_dirs):
    """扫描目录，找出重复版本"""
    # 存储结构: { 基础番号: { 'base_file': '文件名(如果有)', 'suffixes': { '后缀': '文件名' } } }
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
                        # 找到了无后缀的原版
                        db[base_id]['base_file'] = file
                    else:
                        # 找到了带后缀的版本
                        db[base_id]['sub_versions'].append(file)

    # 筛选重复项
    duplicates = {}
    for base_id, info in db.items():
        # 判定条件：原版存在，且至少存在一个带后缀的版本
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

    # Telegram 限制一条消息在 4000 字符左右，安全起见我们设单次发送上限为 3000 字符
    MAX_CHAR = 3000
    
    # 组合第一条消息（总结 + 头部列表）
    current_message = f"🔔 *STRM多版本重复检测报告*\n\n{summary}\n\n"
    current_message += "⚠️ *重复详情列表：*\n"
    
    messages_to_send = []
    
    for detail in detail_list:
        # 如果加入这条详情超长了，就先打包当前条
        if len(current_message) + len(detail) > MAX_CHAR:
            messages_to_send.append(current_message)
            current_message = "🔄 *STRM重复检测报告（续）：*\n\n"
        
        current_message += detail + "\n"
    
    # 把最后剩余的内容加进去
    if current_message:
        messages_to_send.append(current_message)
        
    # 依次调用青龙通知接口发送
    for idx, msg in enumerate(messages_to_send):
        title = f"STRM重复检测 ({idx + 1}/{len(messages_to_send)})"
        log(f"✉️ 正在发送第 {idx + 1} 部分通知...")
        send_notification(title, msg)


def main():
    log("🚀 脚本启动，准备执行重复 STMR 检测...")
    
    # 读取环境变量
    media_dir_env = os.environ.get("MEDIA_DIR")
    if not media_dir_env:
        log("❌ 错误: 未配置环境变量 `MEDIA_DIR`。请在青龙环境变量中添加该变量并填写飞牛 strm 目录路径。")
        if HAS_NOTIFY:
            send_notification("STRM检测失败", "未配置环境变量 `MEDIA_DIR`，脚本已中止。")
        sys.exit(1)
        
    # 支持逗号分隔多路径
    media_dirs = media_dir_env.split(',')
    log(f"📋 待扫描路径列表: {media_dirs}")
    
    # 执行扫描
    duplicates = scan_and_find_duplicates(media_dirs)
    
    if not duplicates:
        summary_msg = "✅ 扫描完成！未发现【原版与多版本共存】的重复 strm 文件。"
        log(summary_msg)
        if HAS_NOTIFY:
            send_notification("STRM检测完成", summary_msg)
        sys.exit(0)
        
    # 存在重复，格式化输出
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
        # 顺便在控制台打印一份，方便排查
        print(f"[{base_id}] 重复: 原版[{files['original']}] <-> 冲突{files['others']}")

    # 发送通知
    send_tg_notification(summary_msg, detail_list)
    log("🏁 任务运行结束。")


if __name__ == '__main__':
    main()
