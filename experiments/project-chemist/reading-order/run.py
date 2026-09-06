"""One-shot frozen-source coverage experiment, not a production selector."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tokenize


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def chunks(path, raw, width):
    text = raw.decode("utf-8")
    lines = text.splitlines(keepends=True)
    assert "\r" not in text, "Frozen source must use LF for character accounting"
    tree = ast.parse(text)
    owners = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    names = {}
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type == tokenize.NAME:
            names.setdefault(token.start[0], set()).add(token.string)
    result = []
    for offset in range(0, len(lines), width):
        start, end = offset + 1, min(offset + width, len(lines))
        symbols = set().union(*(names.get(i, set()) for i in range(start, end + 1)))
        symbols.update(node.name for node in owners if node.lineno <= end and node.end_lineno >= start)
        result.append({"path": path, "sha256": digest(raw), "symbols": symbols,
                       "lines": [{"line": i, "text": lines[i-1]} for i in range(start, end+1)]})
    return result


def take(items, budget):
    selected, used = [], 0
    for item in items:
        rows = []
        for row in item["lines"]:
            if used + len(row["text"]) > budget:
                if rows:
                    selected.append({"path": item["path"], "sha256": item["sha256"], "lines": rows})
                return selected, used
            rows.append(row)
            used += len(row["text"])
        if rows:
            selected.append({"path": item["path"], "sha256": item["sha256"], "lines": rows})
    return selected, used


def select(items, symbols, budget, reserve):
    # No gold ranges enter ranking; ties retain source order.
    scores = [len(set(symbols) & item["symbols"]) for item in items]
    if not any(scores):
        packet, used = take(items, budget)
        return packet, used, {"fallback": True}
    ranked = sorted(zip(scores, items), key=lambda pair: -pair[0])
    relevant, matched_chars = take([item for score, item in ranked if score], budget-reserve)
    controls, control_chars = take([item for score, item in ranked if not score], reserve)
    return relevant + controls, matched_chars + control_chars, {
        "fallback": False, "matched_chars": matched_chars, "nonmatching_chars": control_chars}


def verify(packet, sources, budget):
    seen, used = set(), 0
    for part in packet:
        raw = sources[part["path"]]
        assert part["sha256"] == digest(raw)
        lines = raw.decode("utf-8").splitlines(keepends=True)
        for row in part["lines"]:
            key = part["path"], row["line"]
            assert key not in seen, "Duplicate source coordinate"
            assert 1 <= row["line"] <= len(lines)
            assert row["text"] == lines[row["line"]-1], "Source text mismatch"
            seen.add(key)
            used += len(row["text"])
    assert used <= budget
    return seen, used


def score(packet, gold, sources, budget):
    seen, used = verify(packet, sources, budget)
    details = []
    for fact in gold:
        count = len(sources[fact["path"]].decode("utf-8").splitlines())
        assert 1 <= fact["start"] <= fact["end"] <= count
        required = {(fact["path"], i) for i in range(fact["start"], fact["end"]+1)}
        details.append({"id": fact["id"], "critical": fact["critical"],
                        "covered_lines": len(seen & required), "required_lines": len(required),
                        "hit": required <= seen})
    return {"chars": used, "hits": sum(f["hit"] for f in details), "total": len(details),
            "critical_misses": [f["id"] for f in details if f["critical"] and not f["hit"]],
            "facts": details, "traceability_valid": True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    repo = here.parents[2]
    spec_raw = (here / "spec.json").read_bytes()
    spec = json.loads(spec_raw)
    sources = {path: subprocess.run(["git", "-C", str(repo), "show", f"{spec['source_commit']}:{path}"],
                                   check=True, capture_output=True).stdout for path in spec["files"]}
    items = [item for path, raw in sources.items() for item in chunks(path, raw, spec["chunk_lines"])]
    packets = {}
    for question in spec["questions"]:
        a, _ = take(items, spec["budget"])
        b, _, allocation = select(items, question["symbols"], spec["budget"], spec["reserve"])
        packets[question["id"]] = {"A": a, "B": b, "allocation": allocation}
    results = []
    for question in spec["questions"]:
        packet = packets[question["id"]]
        results.append({"id": question["id"], **{arm: score(packet[arm], question["gold"], sources, spec["budget"])
                                                 for arm in ("A", "B")}})
    passed = (sum(row["B"]["hits"] > row["A"]["hits"] for row in results) >= 2
              and all(row["B"]["hits"] >= row["A"]["hits"] and not row["B"]["critical_misses"] for row in results))
    packet_raw = (json.dumps(packets, ensure_ascii=False, indent=2) + "\n").encode()
    report = {"source_commit": spec["source_commit"], "spec_sha256": digest(spec_raw),
              "runner_sha256": digest(Path(__file__).read_bytes()), "packets_sha256": digest(packet_raw),
              "source_sha256": {path: digest(raw) for path, raw in sources.items()},
              "coverage_gate_passed": passed, "cost_gate": "requires_manual_elapsed_record",
              "model_evaluation_performed": False, "questions": results}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "packets.json").write_bytes(packet_raw)
    (args.output / "results.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
