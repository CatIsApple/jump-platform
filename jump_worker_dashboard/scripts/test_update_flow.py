"""자동 업데이트 배치파일 로직 검증용 데모 스크립트.

빌드 없이 Windows 에서 바로 실행해서 v1.0.9 의 새 업데이트 흐름
(cmd.exe 배치 + 3단계 폴백)을 검증합니다.

사용법:
    cd C:\\Users\\<user>\\Downloads\\jump_platform
    python jump_worker_dashboard\\scripts\\test_update_flow.py

동작:
    1. updater.install_and_restart_windows 를 직접 호출
    2. 배치 파일이 %LOCALAPPDATA%\\jump_worker_dashboard\\ 에 저장됨
    3. cmd.exe 가 백그라운드 실행되어:
       - 부모 프로세스 대기 (이 스크립트는 빠르게 종료됨)
       - 인스톨러 실행 (updates 폴더의 setup.exe 사용)
       - 설치 후 새 앱 자동 실행
    4. 종료 후 로그 확인:
       %LOCALAPPDATA%\\jump_worker_dashboard\\update_py.log  ← Python 단계
       %LOCALAPPDATA%\\jump_worker_dashboard\\update.log     ← 배치 실행 단계
       %LOCALAPPDATA%\\jump_worker_dashboard\\update.bat     ← 실행된 배치 사본
       %LOCALAPPDATA%\\jump_worker_dashboard\\inno_setup.log ← Inno Setup 상세
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    # 프로젝트 루트를 sys.path에 추가하여 updater 모듈 import 가능하게 함
    project_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(project_root))

    # 기존 setup.exe 를 찾기
    setup_candidates = [
        Path(os.path.expanduser("~")) / "jump_worker_dashboard" / "updates" / "GUARDIAN_Jump_Setup.exe",
        Path.cwd() / "GUARDIAN_Jump_Setup.exe",
    ]

    setup_exe: Path | None = None
    for c in setup_candidates:
        if c.exists():
            setup_exe = c
            break

    if setup_exe is None:
        print("ERROR: setup exe 를 찾을 수 없음. 다음 중 하나에 파일을 두세요:")
        for c in setup_candidates:
            print(f"   - {c}")
        print()
        print("또는 명령어 인자로 직접 지정: python test_update_flow.py <setup.exe path>")
        if len(sys.argv) > 1:
            setup_exe = Path(sys.argv[1])
            if not setup_exe.exists():
                print(f"ERROR: 인자 경로도 존재하지 않음: {setup_exe}")
                return 1
        else:
            return 1

    print("=" * 70)
    print(f"[TEST] 자동 업데이트 흐름 검증")
    print(f"[TEST] Setup EXE: {setup_exe} ({setup_exe.stat().st_size:,} bytes)")
    print("=" * 70)

    # 로그 폴더 초기화
    log_dir = Path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))) / "jump_worker_dashboard"
    log_dir.mkdir(parents=True, exist_ok=True)

    for name in ("update_py.log", "update.log", "update.bat", "inno_setup.log"):
        p = log_dir / name
        if p.exists():
            try:
                p.unlink()
                print(f"[TEST] 이전 {name} 삭제됨")
            except Exception as exc:
                print(f"[TEST] {name} 삭제 실패: {exc}")

    print()
    print(f"[TEST] 3초 후 업데이트 흐름 시작...")
    print(f"[TEST] 이 Python 프로세스가 종료되면 배치 파일이 백그라운드에서 실행됩니다.")
    print(f"[TEST] 설치 진행 상황은 다음 로그로 확인하세요:")
    print(f"       {log_dir / 'update_py.log'}")
    print(f"       {log_dir / 'update.log'}")
    print()
    time.sleep(3)

    # updater.install_and_restart_windows 호출
    # 이 함수는 내부에서 os._exit(0) 하므로 여기서 return 하지 않음
    from jump_worker_dashboard.app import updater  # type: ignore

    try:
        updater.install_and_restart_windows(setup_exe)
    except updater.UpdateError as exc:
        print(f"[TEST] UpdateError: {exc}")
        return 1

    # 도달 안 함 (os._exit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
