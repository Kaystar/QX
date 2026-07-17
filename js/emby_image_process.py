#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
专用于青龙面板的 Emby 媒体目录图片处理脚本

cron: 0 3 * * *
new Env('🖼️Emby海报图片转换器')
---
说明：
1. 本脚本用于处理 Emby 媒体目录下，将视频/strm同名的 `-poster.jpg` 删除，并复制 `-thumb.jpg` 重命名为 `-poster.jpg`。
2. 必须配置环境变量 `MEDIA_DIR`，支持配置多个目录，使用英文逗号 `,` 分隔。
3. 运行结束后，会通过青龙内置的 sendNotify 发送精简的运行总结。
"""

import os
import sys
import shutil

# ==================== 完全替换为你的通知代码 ====================
def send_notify(title, content):
    for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts']:
        if os.path.exists(os.path.join(p, 'sendNotify.py')) and p not in sys.path: 
            sys.path.append(p)
    try:
        from sendNotify import send; send(title, content)
    except Exception as e:
        try:
            from sendNotify import sendNotify; sendNotify(title, content)
        except Exception: 
            print(f"🎉 清理网络卡片网关回执: {e}")
# ================================================================

# 视频与 strm 的常见后缀
VIDEO_EXTENSIONS = ('.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.strm', '.ts')

def process_emby_images():
    # 获取环境变量
    media_dir_env = os.getenv("MEDIA_DIR")
    if not media_dir_env:
        print("❌ 未检测到环境变量 `MEDIA_DIR`，请在青龙面板中配置该环境变量！")
        send_notify("Emby图片转换失败", "未配置环境变量 MEDIA_DIR，任务终止。")
        return

    # 支持逗号分隔的多个目录
    target_dirs = [d.strip() for d in media_dir_env.split(",") if d.strip()]
    
    total_processed = 0
    total_deleted = 0
    total_copied = 0
    total_errors = 0
    detail_logs = []

    def log(msg):
        print(msg)
        sys.stdout.flush()
        detail_logs.append(msg)

    log("🚀 Emby 媒体图片处理任务开始...")
    
    for base_dir in target_dirs:
        if not os.path.exists(base_dir):
            log(f"⚠️ 目录不存在，已跳过: {base_dir}")
            total_errors += 1
            continue
        
        log(f"🔍 正在扫描目录: {base_dir}")
        
        # 遍历目录
        for root, _, files in os.walk(base_dir):
            for file in files:
                # 判断是否为视频文件或 strm
                if file.lower().endswith(VIDEO_EXTENSIONS):
                    # 获取不带后缀的文件名（即番号/视频名）
                    base_name, _ = os.path.splitext(file)
                    
                    # 拼接对应的海报和缩略图路径
                    poster_path = os.path.join(root, f"{base_name}-poster.jpg")
                    thumb_path = os.path.join(root, f"{base_name}-thumb.jpg")
                    
                    # 如果缩略图存在，才进行后续操作（避免误删 poster 后无法恢复）
                    if os.path.exists(thumb_path):
                        # 1. 检查并删除原有的 poster.jpg
                        if os.path.exists(poster_path):
                            try:
                                os.remove(poster_path)
                                total_deleted += 1
                                log(f"🗑️ 已删除旧海报: {file} -> {base_name}-poster.jpg")
                            except Exception as e:
                                total_errors += 1
                                log(f"❌ 删除海报失败: {poster_path}, 原因: {e}")
                                continue
                        
                        # 2. 复制 thumb 并重命名为 poster
                        try:
                            shutil.copy2(thumb_path, poster_path)
                            total_copied += 1
                            total_processed += 1
                            log(f"🔄 已用 thumb 替换 poster: {base_name}")
                        except Exception as e:
                            total_errors += 1
                            log(f"❌ 复制海报失败: {thumb_path} -> {poster_path}, 原因: {e}")
                    else:
                        # 仅做记录，不作为严重错误
                        pass

    log("🏁 任务运行结束。")
    
    # 构造总结通知内容（只发送总结）
    summary_title = "🖼️ Emby海报转换任务完成"
    summary_content = (
        f"处理目录: {', '.join(target_dirs)}\n"
        f"成功替换海报数: {total_processed} 个\n"
        f"删除旧海报数: {total_deleted} 个\n"
        f"新复制海报数: {total_copied} 个\n"
        f"异常/错误次数: {total_errors} 次\n"
        f"详细运行日志请前往青龙面板查看。"
    )
    
    # 调用你的 send_notify 发送通知
    send_notify(summary_title, summary_content)

if __name__ == "__main__":
    process_emby_images()
