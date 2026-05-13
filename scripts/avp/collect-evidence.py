#!/usr/bin/env python3
import json
import sys
import os
from datetime import datetime
from pathlib import Path

def main():
    """Aggregate AVP step evidence into comprehensive report."""

    evidence_dir = Path("/tmp/avp-evidence")
    evidence_dir.mkdir(exist_ok=True)

    steps_summary = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "workflow": "post-deploy-avp",
        "steps": {}
    }

    step_files = [
        ("/tmp/avp-step-1.log", "step_1"),
        ("/tmp/avp-step-2.log", "step_2"),
        ("/tmp/avp-step-3.log", "step_3"),
        ("/tmp/avp-step-4.log", "step_4"),
        ("/tmp/avp-step-5.log", "step_5"),
        ("/tmp/avp-step-6.log", "step_6"),
        ("/tmp/avp-step-7.log", "step_7"),
        ("/tmp/avp-step-8.log", "step_8"),
        ("/tmp/avp-step-9.log", "step_9"),
    ]

    for log_file, step_name in step_files:
        step_info = {
            "name": step_name,
            "log_file": log_file,
            "passed": False,
            "evidence": []
        }

        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                content = f.read()
                # Check for PASS keyword in logs
                if "PASS" in content:
                    step_info["passed"] = True
                # Extract key lines
                for line in content.split('\n'):
                    if 'OK:' in line or 'PASS' in line or 'verified' in line:
                        step_info["evidence"].append(line.strip())
        else:
            step_info["passed"] = False
            step_info["evidence"].append(f"Log file not found: {log_file}")

        steps_summary["steps"][step_name] = step_info

    # Check for additional evidence files
    if os.path.exists("/tmp/avp-step-2-dag.json"):
        try:
            with open("/tmp/avp-step-2-dag.json", 'r') as f:
                steps_summary["steps"]["step_2"]["dag_evidence"] = json.load(f)
        except json.JSONDecodeError:
            pass

    if os.path.exists("/tmp/avp-step-9-summary.json"):
        try:
            with open("/tmp/avp-step-9-summary.json", 'r') as f:
                steps_summary["steps"]["step_9"]["health_summary"] = json.load(f)
        except json.JSONDecodeError:
            pass

    # Calculate overall status
    passed_count = sum(1 for s in steps_summary["steps"].values() if s.get("passed", False))
    total_count = len(steps_summary["steps"])

    steps_summary["summary"] = {
        "total_steps": total_count,
        "passed_steps": passed_count,
        "failed_steps": total_count - passed_count,
        "all_passed": passed_count == total_count
    }

    # Output JSON
    output_file = evidence_dir / "post-deploy-avp.json"
    with open(output_file, 'w') as f:
        json.dump(steps_summary, f, indent=2)

    print(f"Evidence collected to {output_file}")
    print(json.dumps(steps_summary["summary"], indent=2))

    return 0 if steps_summary["summary"]["all_passed"] else 1

if __name__ == "__main__":
    sys.exit(main())
