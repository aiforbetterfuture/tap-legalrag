"""
데모: 직접 작성한 합성 질문 7건을 판정 게이트로 실행해 결과를 보여 준다.
CLI(`python -m law_rag ask`)·평가·시연 화면과 **같은 결정 경로**(pipeline.decide → decide_for_query)를 쓴다.

실행: PYTHONPATH=src python demo.py
      (Windows PowerShell: $env:PYTHONPATH="src"; $env:PYTHONUTF8="1"; python demo.py)
"""
import io
import json
import sys
from pathlib import Path

# Windows 콘솔에서 한글이 깨지지 않도록 UTF-8로 출력
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from law_rag import build_default_pipeline  # noqa: E402
from law_rag.knowledge.loader import build_edges, load_corpus  # noqa: E402

LABEL = {"ANSWER": "근거를 붙여 답변", "CONFIRM": "담당자 확인 요청",
         "ABSTAIN": "보류 · 담당자에게 넘김", "CORRECT": "전제 교정"}


def main() -> int:
    examples = json.loads((ROOT / "examples" / "synthetic" / "examples.json").read_text(encoding="utf-8"))["examples"]
    corpus = load_corpus(ROOT / "tests" / "fixtures", split=True)
    pipe = build_default_pipeline(corpus, build_edges(corpus))
    for i, e in enumerate(examples, 1):
        r = pipe.decide(e["q"], config="B4")
        print("=" * 72)
        print(f"[{i}] 질문: {e['q']}")
        print(f"    결정: {r['decision']} ({LABEL.get(r['decision'], '')})")
        print(f"    사유: {r['reason']}")
        print(f"    경로: {' → '.join(r['path'])}")
        print(f"    검색 상위 3건: " + " · ".join(f"{x['ref']}({x['score']})" for x in r.get("top3_evidence", [])))
        if e.get("known_failure"):
            print(f"    ※ 알려진 결함 사례: {e.get('why', '')}")
    print("=" * 72)
    print("판정 과정을 화면으로 보려면: PYTHONPATH=src python scripts/build_demo_viewer.py → docs/demo/viewer.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
