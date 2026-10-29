"""Turn an evidence bundle into a short Markdown facts sheet.

The sheet states facts only. The "Reviewer notes" column is left empty on
purpose: statuses (Verified / Supported / Missing / Contradicted / Unable to
assess) are assigned by the reviewer, not by this script.
"""

from __future__ import annotations


def _yn(value) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def _facts(r: dict) -> list[tuple[str, str]]:
    f = r["facts"]
    key = r["check"]
    rows: list[tuple[str, str]] = []
    if key == "identity":
        rows.append(("Account / region", f"{f.get('account')} / {f.get('region')}"))
        rows.append(("Caller", str(f.get("caller_arn"))))
    elif key == "network":
        for v in f.get("vpcs", []):
            label = f"VPC {v.get('name') or v['vpc_id']}" + (" (default)" if v.get("is_default") else "")
            kinds: dict[str, int] = {}
            for s in v["subnets"]:
                kinds[s["kind"]] = kinds.get(s["kind"], 0) + 1
            rows.append((label, f"AZs: {', '.join(v['azs']) or 'none'}; subnets: " + (", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) or "none")))
            rows.append((f"{label} NAT gateways", f"{len(v['nat_gateways'])} (AZs: {', '.join(v['nat_gateway_azs']) or 'none'})"))
    elif key == "containers":
        for c in f.get("eks_clusters", []):
            rows.append(
                (
                    f"EKS {c['name']}",
                    f"version {c.get('version')}, status {c.get('status')}; public endpoint {_yn(c.get('endpoint_public_access'))}"
                    f" (CIDRs {', '.join(c.get('public_access_cidrs') or []) or 'n/a'}); private endpoint {_yn(c.get('endpoint_private_access'))}",
                )
            )
            rows.append((f"EKS {c['name']} logs", ", ".join(c.get("control_plane_logs") or []) or "none enabled"))
            for ng in c.get("nodegroups", []):
                rows.append((f"EKS {c['name']} node group {ng['name']}", f"AZs: {', '.join(ng['azs'])}"))
        for c in f.get("ecs_clusters", []):
            rows.append((f"ECS {c['name']}", f"status {c.get('status')}, services {c.get('active_services')}, tasks {c.get('running_tasks')}"))
        if not f.get("eks_clusters") and not f.get("ecs_clusters"):
            rows.append(("Clusters", "none found in this region"))
    elif key == "databases":
        for d in f.get("db_instances", []):
            rows.append(
                (
                    f"RDS {d['id']} ({d.get('engine')})",
                    f"Multi-AZ {_yn(d.get('multi_az'))}; backup retention {d.get('backup_retention_days')} d; encrypted {_yn(d.get('storage_encrypted'))};"
                    f" public {_yn(d.get('publicly_accessible'))}; deletion protection {_yn(d.get('deletion_protection'))}",
                )
            )
        for c in f.get("db_clusters", []):
            rows.append((f"RDS cluster {c['id']}", f"Multi-AZ {_yn(c.get('multi_az'))}; members {c.get('members')}; retention {c.get('backup_retention_days')} d"))
        if not rows:
            rows.append(("RDS", "no instances or clusters found"))
    elif key == "backups":
        for p in f.get("backup_plans", []):
            rules = "; ".join(f"{r['name']} {r['schedule']} keep {r['delete_after_days']} d" for r in p["rules"]) or "no rules"
            sel = "not readable" if p["selections"] is None else (", ".join(p["selections"]) or "none")
            rows.append((f"Backup plan {p['name']}", f"{rules}; selections: {sel}"))
        if not rows:
            rows.append(("AWS Backup", "no backup plans"))
    elif key == "iam":
        for u in f.get("users", []):
            keys = ", ".join(f"…{k['key_id_suffix']} {k['status']}" for k in u["access_keys"]) or "none"
            rows.append(
                (
                    f"IAM user {u['name']}",
                    f"admin {_yn(u['admin'])}" + (f" ({'; '.join(u['admin_via'])})" if u["admin_via"] else "")
                    + f"; console {_yn(u['console_password'])}; MFA devices {u['mfa_devices']}; access keys: {keys}",
                )
            )
        rows.append(("OIDC providers", ", ".join(f.get("oidc_providers", [])) or "none"))
        for r_ in f.get("oidc_federated_roles", []):
            rows.append((f"Role {r_['role']}", f"trusts {r_['oidc_provider']}"))
    elif key == "cloudtrail":
        for t in f.get("trails", []):
            rows.append(
                (
                    f"Trail {t['name']}",
                    f"logging {_yn(t.get('is_logging'))}; multi-region {_yn(t.get('multi_region'))}; log file validation {_yn(t.get('log_file_validation'))};"
                    f" bucket {t.get('s3_bucket')}",
                )
            )
        if not rows:
            rows.append(("CloudTrail", "no trails"))
    elif key == "waf":
        rows.append(("Regional web ACLs", ", ".join(a["name"] for a in f.get("regional_web_acls", [])) or "none"))
        for lb in f.get("public_albs", []):
            rows.append((f"Public ALB {lb['name']}", "web ACL lookup failed" if lb["lookup_failed"] else f"web ACL: {lb['web_acl'] or 'none'}"))
    elif key == "tagging":
        total = len(f.get("resources", []))
        rows.append(("Required tags", ", ".join(f.get("required_tags", []))))
        rows.append(("Resources checked", str(total)))
        for res in f.get("missing_any", []):
            rows.append((f"{res['type']} {res['id']}", "missing: " + ", ".join(res["missing"])))
        for res in f.get("unreadable", []):
            rows.append((f"{res['type']} {res['id']}", "tags not readable"))
    elif key == "change_events":
        rows.append(("Window", f"{f.get('window_start')} to {f.get('window_end')}"))
        events = f.get("events", [])
        rows.append(("Events found", str(len(events))))
        for e in events[:15]:
            rows.append((f"{e['time']} {e['event']}", f"by {e.get('user')} on {', '.join(x for x in e['resources'] if x) or '-'}"))
    elif key == "well_architected":
        for w in f.get("workloads", []):
            rows.append((f"WA workload {w['name']}", f"lenses {', '.join(w['lenses'])}; risks {w['risk_counts']}; milestones {len(w['milestones'])}"))
        if not rows:
            rows.append(("Well-Architected Tool", "no workloads"))
    return rows


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_summary(results: list[dict], meta: dict) -> str:
    lines = [
        "# Evidence summary",
        "",
        f"- Collected by: evidence-kit (read-only guard on), mode: **{meta.get('mode')}**",
        f"- Region: {meta.get('region')} · Started: {meta.get('started_at')}",
        "- This sheet lists facts only. Statuses are assigned by the reviewer using the rubric in `method.md`.",
        "",
        "| Evidence | Fact | Observed value | Reviewer notes |",
        "|---|---|---|---|",
    ]
    for r in results:
        status = r["collection_status"]
        tag = r["evidence_id"] + ("" if status == "complete" else f" ({status.replace('_', ' ')})")
        if status != "not_collected":
            # A check that collected nothing must not print "none found": that would read as a fact.
            for label, value in _facts(r):
                lines.append(f"| {tag} | {_cell(label)} | {_cell(value)} | |")
        grouped: dict[str, list[str]] = {}
        for err in r["errors"]:
            grouped.setdefault(err["error"][:160], []).append(err["call"])
        for message, calls in grouped.items():
            label = calls[0] if len(calls) == 1 else f"{calls[0]} and {len(calls) - 1} similar calls"
            lines.append(f"| {tag} | NOT COLLECTED: {_cell(label)} | {_cell(message)} | |")
    for note in meta.get("notes", []):
        lines.append(f"\n> {note}")
    lines.append("")
    return "\n".join(lines)
