# -*- coding: utf-8 -*-
"""命令行入口：参数解析、输入收集、目录搜索模式与批量下载流程。"""

import argparse
import re
import sys
from pathlib import Path

import requests

from .errors import DownloadError
from .logutil import attach_console
from .catalog import (fetch_catalog_index, search_catalog, catalog_facets,
                      detail_page_url, DIM_LABELS, FILTER_DIMS)
from .download import AuthContext, initial_token, process_one, _result

def load_links_from_file(path):
    """从文本文件读取批量输入：每行一个链接/ID，忽略空行与 # 注释行"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = p.read_text(encoding="gbk", errors="replace")
    entries = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            entries.append(line)
    return entries



def interactive_input():
    """交互模式：粘贴多行链接/ID，空行结束"""
    print("请粘贴教材详情页链接或 contentId（可一次粘贴多行），输入空行后开始下载:")
    entries = []
    while True:
        try:
            line = input().strip()
        except EOFError:
            break
        if not line:
            break
        entries.append(line)
    return entries



def parse_selection(text, total):
    """解析 '1,3-5' 形式的编号选择，返回 0 基去重升序索引列表。"""
    picked = set()
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            picked.update(n - 1 for n in range(a, b + 1) if 1 <= n <= total)
        elif part.isdigit():
            n = int(part)
            if 1 <= n <= total:
                picked.add(n - 1)
    return sorted(picked)



def _pad(text, width):
    """按显示宽度补齐/截断（中文按 2 列计），保证表格对齐。"""
    text = text or ""
    width_sum = lambda s: sum(2 if ord(c) > 127 else 1 for c in s)
    if width_sum(text) <= width:
        return text + " " * (width - width_sum(text))
    out, used = "", 0
    for c in text:
        cw = 2 if ord(c) > 127 else 1
        if used + cw > width - 1:
            break
        out += c
        used += cw
    return out + "…"



def print_catalog_matches(matches, limit=200):
    headers = ("No", "学段", "年级", "科目", "版本", "册次", "书名")
    widths = (4, 10, 7, 13, 11, 7, 46)
    print("  ".join(_pad(h, w) for h, w in zip(headers, widths)).rstrip())
    for i, it in enumerate(matches, 1):
        if i > limit:
            print(f"  ...（其余 {len(matches) - limit} 条省略）")
            break
        pub = it.get("publisher") or "-"
        if it.get("pub_variant"):
            pub = f"{pub}（{it['pub_variant']}）"
        row = (str(i), it.get("phase") or "-", it.get("grade") or "-",
               it.get("subject") or "-", pub,
               it.get("volume") or it.get("module") or "-", it.get("title") or "")
        print("  ".join(_pad(v, w) for v, w in zip(row, widths)).rstrip())



def print_facets(items, filters=None, keyword=None):
    """打印级联候选值：便于命令行下逐级收窄条件（--show-facets）。"""
    result = catalog_facets(items, filters, keyword=keyword)
    print(f"\n当前条件下命中 {result['total']} 本，各维度可选值：")
    for dim in FILTER_DIMS:
        opts = result["facets"].get(dim) or []
        if not opts:
            continue
        shown = ", ".join(f"{o['value']}({o['count']})" for o in opts[:12])
        more = f"  …等 {len(opts)} 项" if len(opts) > 12 else ""
        print(f"  {DIM_LABELS.get(dim, dim):<5} {shown}{more}")



def run_catalog_search(args):
    """--search/--phase/--grade/--subject/--publisher 触发的目录搜索模式。"""
    print("正在加载教材目录（首次约 40MB，之后 7 天内走本地缓存）...")
    try:
        items = fetch_catalog_index(timeout=args.timeout,
                                    on_log=lambda m: print(f"  {m}"))
    except (DownloadError, requests.RequestException) as e:
        print(f"目录获取失败: {e}")
        return 1

    filters = {d: getattr(args, d, None) for d in FILTER_DIMS}
    if args.show_facets:
        print_facets(items, filters, keyword=args.search)
        return 0

    matches = search_catalog(items, keyword=args.search,
                             include_resources=args.include_resources, **filters)
    if not matches:
        print("没有匹配的教材。可尝试：放宽筛选条件 / 换个关键词 / 只用 --search。")
        return 1

    print(f"\n共匹配 {len(matches)} 本教材：\n")
    print_catalog_matches(matches)

    if args.list_only:
        return 0
    if args.all:
        picked = matches
    elif sys.stdin and sys.stdin.isatty():
        try:
            raw = input("输入要下载的编号（如 1,3-5；a=全部；回车取消）: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw:
            print("已取消。")
            return 0
        if raw.lower() in ("a", "all", "全部"):
            picked = matches
        else:
            idx = parse_selection(raw, len(matches))
            if not idx:
                print("未选中任何有效编号。")
                return 1
            picked = [matches[i] for i in idx]
    else:
        print("\n（非交互环境仅列出结果；加 --all 可直接下载全部匹配项）")
        return 0

    ordered = [detail_page_url(m["id"]) for m in picked]
    print(f"\n已选择 {len(ordered)} 本，开始下载...")
    return run_downloads(ordered, args)



# ---------- 汇总输出 ----------
def print_summary(results):
    ok = [r for r in results if r["status"] == "ok"]
    skip = [r for r in results if r["status"] == "skip"]
    fail = [r for r in results if r["status"] == "fail"]
    print("\n" + "=" * 52)
    print(f"下载汇总: 成功 {len(ok)} | 跳过 {len(skip)} | 失败 {len(fail)} | 共 {len(results)}")
    if fail:
        print("-" * 52)
        print("失败明细:")
        for r in fail:
            name = r["title"] or (r["entry"] if len(r["entry"]) <= 40 else r["entry"][:37] + "...")
            print(f"  x {name}: {r['msg']}")
    print("=" * 52)



# ---------- 命令行入口 ----------
def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="smartedu_downloader.py",
        description="书到了（ShudaoLe）· 国家中小学智慧教育平台教材 PDF 下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            '  python smartedu_downloader.py "https://basic.smartedu.cn/tchMaterial/detail?...&contentId=xxxx"\n'
            "  python smartedu_downloader.py <链接或ID1> <链接或ID2> -o \"D:/教材\"\n"
            "  python smartedu_downloader.py -f links.txt -o ./downloads\n"
            "  python smartedu_downloader.py            # 交互模式，粘贴多行链接后空行结束\n"
            "\n"
            "目录搜索（按学段/年级/科目/版本找教材，无需粘贴链接）:\n"
            "  python smartedu_downloader.py --search 语文 --phase 小学 --grade 一年级\n"
            "  python smartedu_downloader.py --subject 数学 --grade 初一 --publisher 人教\n"
            "  python smartedu_downloader.py --phase 高中 --subject 英语 --list-only\n"
            "  python smartedu_downloader.py --phase 小学 --grade 一年级 --subject 语文 --all\n"
            "\n"
            "登录令牌: 平台对 PDF 下载启用了鉴权，需提供浏览器登录后的 x-nd-auth 值\n"
            "(--token 参数 / SMARTEDU_TOKEN 环境变量 / token.txt 文件 / 提示时交互粘贴)。"
        ),
    )
    parser.add_argument("inputs", nargs="*",
                        help="教材详情页链接或 contentId，可同时传多个")
    parser.add_argument("-f", "--file",
                        help="批量文件，每行一个链接或 contentId（# 开头为注释）")
    parser.add_argument("--search", metavar="关键词",
                        help="目录搜索模式：按书名关键词查找（可与下面筛选组合）")
    parser.add_argument("--phase", metavar="学段",
                        help="筛选学段（小学/初中/高中）")
    parser.add_argument("--grade", metavar="年级",
                        help="筛选年级（一年级/初一/高一 等）")
    parser.add_argument("--subject", metavar="科目",
                        help="筛选科目（语文/数学/英语 等）")
    parser.add_argument("--publisher", metavar="版本",
                        help="筛选版本：分组名（人教版系，含 人教版/统编版/人教鄂教版）"
                             "、真实标签（统编版、人教版）或简写（人教）均可")
    parser.add_argument("--volume", metavar="册次",
                        help="筛选册次（上册/下册/全一册 等）")
    parser.add_argument("--audience", metavar="用途",
                        help="筛选用途（教师用书/学生用书 等）")
    parser.add_argument("--system", metavar="学制",
                        help="筛选学制（六三学制/五四学制 等）")
    parser.add_argument("--school", metavar="学校",
                        help="筛选学校类型（普通学校/盲校/聋校/培智学校 等）")
    parser.add_argument("--editor", metavar="主编",
                        help="筛选主编/作者（如 主编:吴欣 或直接 吴欣）")
    parser.add_argument("--module", metavar="模块",
                        help="筛选教材模块（如 选择性必修）")
    parser.add_argument("--all", action="store_true",
                        help="下载搜索命中的全部教材（默认交互选择编号）")
    parser.add_argument("--list-only", action="store_true",
                        help="仅列出搜索结果，不进入下载")
    parser.add_argument("--show-facets", action="store_true",
                        help="级联查看：打印当前条件下各维度可选值与命中数")
    parser.add_argument("--include-resources", action="store_true",
                        help="搜索时也包含课件/课时等非教材资源（默认仅教材）")
    parser.add_argument("-o", "--output", default="downloads",
                        help="输出目录（默认: ./downloads）")
    parser.add_argument("--token", help="X-ND-AUTH 登录令牌")
    parser.add_argument("--retries", type=int, default=3,
                        help="每个下载地址的重试次数（默认: 3）")
    parser.add_argument("--timeout", type=int, default=30,
                        help="单次请求超时秒数（默认: 30）")
    parser.add_argument("--no-save-token", action="store_true",
                        help="不将交互输入的令牌保存到 token.txt")
    parser.add_argument("--flat-name", action="store_true",
                        help="用纯书名命名（默认用「学段科目_版本_年级册次」结构化命名）")
    return parser



def run_downloads(ordered, args):
    """下载队列主循环（链接模式与目录搜索模式共用），返回退出码。"""
    out_dir = Path(args.output).expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"创建输出目录失败: {e}")
        return 1

    auth = AuthContext(initial_token(args.token), save_token=not args.no_save_token)
    print(f"输出目录: {out_dir.resolve()}")
    if auth.token:
        print("已加载登录令牌（来自 命令行/环境变量/token.txt）")
    print(f"共 {len(ordered)} 条待处理")

    results = []
    try:
        for i, entry in enumerate(ordered, 1):
            preview = entry if len(entry) <= 72 else entry[:69] + "..."
            print(f"\n[{i}/{len(ordered)}] {preview}")
            try:
                results.append(process_one(entry, out_dir, auth,
                                           args.retries, args.timeout,
                                           flat_name=args.flat_name))
            except KeyboardInterrupt:
                raise
            except Exception as e:  # 兜底：任何未预料异常不中断批量任务
                results.append(_result(entry, None, "fail",
                                       f"未预料异常: {type(e).__name__}: {e}"))
            status = results[-1]
            if status["status"] == "ok":
                print(f"  下载完成: {status['msg']}")
            elif status["status"] == "skip":
                print(f"  {status['msg']}")
            else:
                print(f"  失败: {status['msg']}")
    except KeyboardInterrupt:
        print("\n\n用户中断，正在退出...")

    print_summary(results)
    return 1 if any(r["status"] == "fail" for r in results) else 0



def main(argv=None):
    attach_console()  # P1-3：logging 输出走裸格式控制台，与旧版 print 一致
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # 目录搜索模式：给了任一搜索/筛选参数即触发
    if any([args.search, args.phase, args.grade, args.subject, args.publisher]):
        return run_catalog_search(args)

    # 收集输入：位置参数 + 文件；都为空且是终端 -> 交互模式
    entries = list(args.inputs)
    if args.file:
        try:
            entries.extend(load_links_from_file(args.file))
        except OSError as e:
            print(f"读取批量文件失败: {e}")
            return 1
    if not entries:
        if sys.stdin and sys.stdin.isatty():
            entries = interactive_input()
        else:
            parser.print_help()
            return 1

    # 去重并保持顺序
    ordered = list(dict.fromkeys(e.strip() for e in entries if e.strip()))
    if not ordered:
        print("未提供任何有效链接。")
        return 1

    return run_downloads(ordered, args)




if __name__ == "__main__":
    sys.exit(main())
