# coding: utf-8
"""rxlib 无窗口常驻启动器（供任务计划/后台静默运行使用）。

职责：
1. 根据当前版本 app/current/manifest.json 选定便携 Python 运行时；
2. 配置隔离依赖环境变量（与 run.ps1 保持一致）；
3. 把 stdout/stderr 重定向到 logs/launcher-YYYYMMDD.log，
   因为 pythonw.exe 没有控制台，崩溃信息默认会丢失；
4. 以 streamlit run 方式启动平台，并阻塞驻留。

手动临时运行请直接使用 run.ps1；本启动器主要被计划任务调用。
端口可用环境变量 RXLIB_PORT 覆盖，默认 8504。
"""
import datetime
import json
import os
import sys


def _bootstrap():
    root = os.path.dirname(os.path.abspath(__file__))
    app_dir = os.path.join(root, 'app', 'current')
    manifest_path = os.path.join(app_dir, 'manifest.json')

    runtime_name = 'python310'
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            runtime_name = json.load(f).get('runtime') or runtime_name
    except Exception:
        pass

    pylibs = os.path.join(root, 'runtime', runtime_name, 'pylibs')
    for p in (pylibs, app_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    os.environ['RXLIB_HOME'] = root
    os.environ.setdefault('PYTHONNOUSERSITE', '1')
    os.environ['NO_PROXY'] = 'localhost,127.0.0.1'
    os.environ['no_proxy'] = 'localhost,127.0.0.1'

    logs_dir = os.path.join(root, 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%d')
    log_path = os.path.join(logs_dir, 'launcher-%s.log' % stamp)
    log = open(log_path, 'a', encoding='utf-8', buffering=1)
    sys.stdout = log
    sys.stderr = log

    entry = os.path.join(app_dir, 'factor_lab_ui.py')
    port = os.environ.get('RXLIB_PORT', '8504')

    print('\n[%s] launcher starting, port=%s, runtime=%s'
          % (datetime.datetime.now().isoformat(timespec='seconds'),
             port, runtime_name))
    return entry, port


def main():
    entry, port = _bootstrap()
    # 构造 streamlit CLI 参数并运行（阻塞，进程退出即任务结束）
    sys.argv = [
        'streamlit', 'run', entry,
        '--server.port=%s' % port,
        '--server.address=0.0.0.0',
        '--server.headless=true',
        '--global.developmentMode=false',
    ]
    from streamlit.web import cli as stcli
    stcli.main()


if __name__ == '__main__':
    try:
        main()
    except SystemExit as e:
        print('streamlit exited with code %s' % e.code)
        raise
    except Exception:
        import traceback
        traceback.print_exc()
        raise
