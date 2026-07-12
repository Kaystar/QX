# -*- coding: utf-8 -*-
# ql author: director_cleaner
# ql name: 🎬 nfo导演标签清理
# ql cron: 0 0 3 * * *
# ql desc: 拉网式快速剔除全盘NFO中的director标签，保持完美行距不留白
# ==================== 🛠️ 青龙环境变量配置指南 ====================
# 1. MEDIA_DIR        : 媒体库挂载路径。  [默认值: /movies]
# =================================================================

import os, re, sys

MEDIA_DIR = os.getenv("MEDIA_DIR", "/movies").strip()

def process_nfo_director(nfo_path):
    if not os.path.exists(nfo_path): return False
    try:
        with open(nfo_path, "r", encoding="utf-8", errors="ignore") as f: content = f.read()
        if "<director>" in content:
            # 连同导演行尾部的换行回车一并整体斩除，确保100%无空白行残留
            content_new = re.sub(r'<director>.*?</director>\s*', '', content, flags=re.DOTALL)
            with open(nfo_path, "w", encoding="utf-8") as f: f.write(content_new)
            print(f"    ✂️ [清除] 路径: {nfo_path} 删除了 <director>")
            return True
    except Exception: pass
    return False

def send_notify(title, content):
    for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts']:
        if os.path.exists(os.path.join(p, 'sendNotify.py')) and p not in sys.path: sys.path.append(p)
    try:
        from sendNotify import send; send(title, content)
    except Exception as e:
        try:
            from sendNotify import sendNotify; sendNotify(title, content)
        except Exception: print(f"🎉 清理网络卡片网关回执: {e}")

def main():
    print(f"==========================================\n🎬 导演标签全盘无条件剔除工具\n==========================================")
    if not os.path.exists(MEDIA_DIR): print(f"❌ 挂载路径不存在: {MEDIA_DIR}"); return
    
    print(f"🚀 启动全盘导演雷达拉网大扫除...\n📂 扫描路径：{MEDIA_DIR}")
    scanned_nfo, cleaned_count = 0, 0
    for r, _, files in os.walk(MEDIA_DIR):
        for f in files:
            if f.lower().endswith('.nfo'):
                scanned_nfo += 1
                if process_nfo_director(os.path.join(r, f)): cleaned_count += 1
                
    print(f"==========================================\n📊 扫描结束！全盘共核对 {scanned_nfo} 个 NFO，成功剔除了 {cleaned_count} 个导演标签。\n==========================================")
    
    if cleaned_count > 0:
        content = f"📂 扫描路径：【{MEDIA_DIR}】\n📊 扫描总数：【全盘共核对 {scanned_nfo} 个 NFO 文件】\n✂️ 清理结果：【累计无条件剔除 {cleaned_count} 个导演标签，排版行距极其完美】"
        send_notify("🎬 nfo导演标签清理", content.strip())

if __name__ == "__main__":
    main()
