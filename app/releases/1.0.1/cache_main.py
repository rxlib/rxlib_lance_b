import argparse

import platform_env
import factor_lab as fl


def main():
    ap = argparse.ArgumentParser(description='裁剪本地行情月片缓存')
    ap.add_argument('--keep', type=int, default=6, choices=(3, 6),
                    help='保留最近 N 个自然月（含最新月）')
    ap.add_argument('--apply', action='store_true',
                    help='真正执行删除；缺省为预览')
    args = ap.parse_args()
    r = fl.prune_local_cache(args.keep, dry_run=not args.apply)
    print('裁剪基准月(含当月保留):', r['cutoff'])
    print('保留月片:', ', '.join(r['kept_shards']) or '(无)')
    print('删除月片:', ', '.join(r['deleted_shards']) or '(无)')
    print('删除面板:', len(r['deleted_panels']), '个')
    if args.apply:
        print('已完成裁剪。')
    else:
        print('当前为预览，未真正删除。确认无误后加 --apply 执行。')


if __name__ == '__main__':
    main()
