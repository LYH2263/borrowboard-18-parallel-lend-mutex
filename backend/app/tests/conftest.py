import sys

def pytest_runtest_logreport(report):
    # 失败时把场景名（含中文参数化 id）打到输出，不靠人工对行号。
    if report.failed and report.when == "call":
        sys.stdout.write(f"\n!!! 场景失败: {report.head_line}\n")
        sys.stdout.flush()
