# -*- coding: utf-8 -*-
# ql author: metadata_cleaner
# ql name: 🗑️ 无视频元数据清理
# ql cron: 0 4 * * *
# ql desc: 1:1硬核对齐strm视频，物理擦除失去主人的无主图片与NFO垃圾
# ==================== 🛠️ 青龙环境变量配置指南 ====================
# 1. MEDIA_DIR        : 媒体库挂载路径。  [默认值: /movies]
# =================================================================

import os, sys

MEDIA_DIR = os.getenv("MEDIA_DIR", "/movies").strip()
M_COUNTS = {"nfo": 0, "-poster.jpg": 0, "-fanart.jpg": 0, "-thumb.jpg": 0}

def clean_orphan_absolute_safety(root_dir, files):
    strm_bases = {os.path.splitext(f)[0] for f in files if f.lower().endswith('.strm')}
    
    for f in files:
        if f.lower().endswith('.nfo') or f.lower().endswith('.jpg'):
            bn, suf = os.path.splitext(f)[0], ""
            for s in ['-poster', '-fanart', '-thumb']:
                if bn.endswith(s) or bn.endswith(s.upper()): suf = s + ".jpg"; bn = bn[:-len(s)]; break
            if not suf: suf = "nfo" if f.lower().endswith('.nfo') else "jpg"
            
            if bn not in strm_bases:
                try:
                    os.remove(os.path.join(root_dir, f))
                    M_COUNTS[suf] = M_COUNTS.get(suf, 0) + 1
                    print(f"    清  [孤儿清理] 视频不存在，物理擦除: {f}")
                except Exception: pass

def send_notify(title, content):
    for p in ['/ql/data/scripts', '/ql/scripts', '/ql/repo/scripts']:
        if os.path.exists(os.path.join(p, 'sendNotify.py')) and p not in sys.path: sys.path.append(p)
    try:
        from sendNotify import send; send(title, content)
    except Exception as e:
        try:
            from sendNotify import sendNotify; sendNotify(title, content)
        except Exception: print(f"🎉 垃圾清理网络卡片网关回执: {e}")

def main():
    print(f"==========================================\n🧹 孤儿无主垃圾元数据物理清除工具\n==========================================")
    if not os.path.exists(MEDIA_DIR): print(f"❌ 挂载路径不存在: {MEDIA_DIR}"); return
    
    print(f"🚀 启动整盘死链残留大图扫描清除...\n📂 扫描路径：{MEDIA_DIR}")
    for r, _, files in os.walk(MEDIA_DIR):
        clean_orphan_absolute_safety(r, files)
                
    total_cleaned = sum(M_COUNTS.values())
    print(f"==========================================\n🧹 清理结束！本次共物理擦除 {total_cleaned} 个残留无主垃圾文件。\n==========================================")
    
    if total_cleaned > 0:
        content = f"📂 扫描路径：【{MEDIA_DIR}】\n🧹 清理结果：【共清除 {total_cleaned} 个无主元数据文件】"
        if M_COUNTS.get("nfo", 0) > 0: content += f"\n  ├── 📄 NFO文件: {M_COUNTS['nfo']} 个"
        if M_COUNTS.get("-poster.jpg", 0) > 0: content += f"\n  ├── 🖼️  海报图: {M_COUNTS['-poster.jpg']} 张"
        if M_COUNTS.get("-fanart.jpg", 0) > 0: content += f"\n  ├── 🎨 背景图: {M_COUNTS['-fanart.jpg']} 张"
        if M_COUNTS.get("-thumb.jpg", 0) > 0: content += f"\n  └── 📷 缩略图: {M_COUNTS['-thumb.jpg']} 张"
        send_notify("🗑️ 无视频元数据清理", content.strip())

if __name__ == "__main__":
    main()
