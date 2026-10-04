"""打包前整理 cache 数据：复制到 build_stage/cache 并剔除不随包分发的文件

PyInstaller 的 --add-data 只能整目录打包、不支持排除单个文件，
且 build_exe.bat 必须保持纯 ASCII（写不出 emoji 文件名），
所以排除逻辑放在这里，打包时改用暂存目录作为 cache 的数据源。

用法（build_exe.bat 中在 PyInstaller 之前调用）：
    python stage_cache.py
"""

import os
import shutil

SRC_DIR = "cache"
DST_DIR = os.path.join("build_stage", "cache")

# 打包时排除的文件：scene_😀.json（测试场景，用 \U 转义写出，保持本文件纯 ASCII）
EXCLUDE_NAMES = {"scene_\U0001F600.json"}


def main():
    # 每次重新整理，先清掉上次的暂存内容
    if os.path.isdir(DST_DIR):
        shutil.rmtree(DST_DIR)

    os.makedirs(DST_DIR)

    if not os.path.isdir(SRC_DIR):
        print(f"no {SRC_DIR}/ found, staged cache is empty")
        return

    copied = 0
    skipped = 0

    for root, _dirs, files in os.walk(SRC_DIR):
        rel_dir = os.path.relpath(root, SRC_DIR)
        dst_dir = os.path.join(DST_DIR, rel_dir) if rel_dir != "." else DST_DIR
        os.makedirs(dst_dir, exist_ok=True)

        for file_name in files:
            if file_name in EXCLUDE_NAMES:
                skipped += 1
                continue
            shutil.copyfile(os.path.join(root, file_name), os.path.join(dst_dir, file_name))
            copied += 1

    print(f"staged cache -> {DST_DIR}  (copied: {copied}, excluded: {skipped})")


if __name__ == "__main__":
    main()
