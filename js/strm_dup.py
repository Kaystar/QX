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

青龙通知与结果：
- 消息轰炸优化：Telegram 通知中【仅发送总结数据】，不再发送详细列表。
- 详细结果查看：具体的重复番号列表将自动写入脚本同级目录下的 `🔢视频版本检测结果.txt` 文件中。
=========================================
"""

import os
import re
import sys

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
    """打印详细运行日志"""
    print(f"[日志] {message}")


def extract_base_and_suffix(filename):
    """
    解析文件名/番号，拆分为(基础番号, 后缀)
    匹配常见的后缀连接符：-, _, 空格 加上常见后缀如 C, 4k, 1080p, 60fps, CD1, UNCUT 等
    """
    name_without_ext, _ = os.path.splitext(filename)
    
    # 正则：匹配 [番号] + [分隔符(横杠/下划线/空格)] + [字母/数字后缀]
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


def write_details_to_file(duplicates):
    """将重复详情写入脚本目录下的文本文件中"""
    current_dir = os.path.dirname(os.path.abspath(__file__))
    result_file_path = os.path.join(current_dir, "🔢视频版本检测结果.txt")
    
    try:
        with open(result_file_path, "w", encoding="utf-8") as f:
            f.write("==================================================\n")
            f.write("               🔢 视频版本检测详细报告\n")
            f.write("==================================================\n")
            f.write(f"生成时间: {os.popen('date').read().strip()}\n\n")
            
            for idx, (base_id, files) in enumerate(duplicates.items(), 1):
                f.write(f"{idx}. 番号: {base_id}\n")
                f.write(f"   🔹 原版: {files['original']}\n")
                f.write(f"   🔸 冲突版本:\n")
                for other in files['others']:
                    f.write(f"      - {other}\n")
                f.write("-" * 40 + "\n")
                
        log(f"💾 详细重复列表已成功写入文件: {result_file_path}")
    except Exception as e:
        log(f"❌ 写入结果文件失败: {e}")


def main():
    log("🚀 脚本启动，准备执行重复 STMR 检测...")
    
    media_dir_env = os.environ.get("MEDIA_DIR")
    if not media_dir_env:
        log("❌ 错误: 未配置环境变量 `MEDIA_DIR`。请在青龙环境变量中添加该变量并填写飞牛 strm 目录路径。")
        send_notify("🔢视频版本检测失败", "未配置环境变量 `MEDIA_DIR`，脚本已中止。")
        sys.exit(1)
        
    media_dirs = media_dir_env.split(',')
    log(f"📋 待扫描路径列表: {media_dirs}")
    
    duplicates = scan_and_find_duplicates(media_dirs)
    
    if not duplicates:
        summary_msg = "✅ 扫描完成！未发现【原版与多版本共存】的重复 strm 文件。"
        log(summary_msg)
        send_notify("🔢视频版本检测完成", summary_msg)
        sys.exit(0)
        
    total_count = len(duplicates)
    detail_count = sum(len(v['others']) + 1 for v in duplicates.values())
    
    # 1. 整理控制台日志并保存到本地文件
    log(f"发现 {total_count} 组重复，开始写入本地文件...")
    write_details_to_file(duplicates)
    
    # 2. 组装精简版 Telegram 通知内容（不包含具体列表）
    summary_msg = (
        f"📊 *检测结果总结：*\n"
        f"- 共发现 **{total_count}** 组存在冲突的番号\n"
        f"- 涉及重复 strm 文件共 **{detail_count}** 个。\n\n"
        f"📝 *温馨提示：*\n"
        f"详细的重复名单已写入脚本目录下的 `🔢视频版本检测结果.txt` 文件中，请前往青龙面板“脚本管理”或前往对应文件夹查看。"
    )
    
    # 3. 发送单条总结通知
    log("✉️ 正在发送总结通知...")
    send_notify("🔢视频版本检测报告", summary_msg)
    log("🏁 任务运行结束。")


if __name__ == '__main__':
    main()
