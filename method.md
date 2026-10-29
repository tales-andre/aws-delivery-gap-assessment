# Assessment method: contracted scope vs. delivered AWS work

This is my method for answering a client who asks: *"The vendor says the work is done. What was actually implemented, what is missing, and how complete is the project?"*

The method is independent, evidence-based, read-only and human-led. Scripts collect facts; I make every judgment and sign the report. A worked example about a fictional company is in [`sample-report/`](sample-report/gap-assessment.md).

---

## 1. Principles

1. **Scope first, evidence second.** Contracted tasks and acceptance criteria are written down and agreed with the client *before* I look at evidence, so the bar does not move after the facts are known.
2. **What runs beats what is written.** A document or a status report is a claim until the account, the IaC state or the delivery record confirms it.
3. **Read-only, always.** I never change the client's account, repositories or pipelines. Access is a read-only role, so my API calls appear in the client's own CloudTrail under that role name (for read calls, when the trail records read events).
4. **Reproducible.** Every observation has an evidence ID, a source, a timestamp and a SHA-256 hash. Every number in the report can be re-computed from the scoring file.
5. **Fair to the vendor.** The vendor sees the draft findings and can answer before the report is final. Their answer is quoted, and statuses change only on new evidence.

## 2. Inputs I ask for

| Input | Why | Usual format |
|---|---|---|
| SOW / contract, change requests, acceptance criteria | Defines what was promised and what changed | PDF or document |
| Backlog or task tracker export | Maps contract items to tasks and "done" claims | CSV export or read-only access |
| Vendor status reports and handover documents | The claims to be tested | PDF or document |
| Repository access (read-only) | Commits, branches, tags, CI/CD and IaC definitions | Clone or read-only access |
| IaC state access (read-only) | What Terraform believes is deployed | Read on the state object only |
| AWS account access: an IAM role I can assume | What is actually running | Role with `SecurityAudit` + `ReadOnlyAccess`, plus a deny on data reads ([templates](evidence-kit/iam/)) |
| CI/CD run history and deployment records | Whether pipelines ran and what they deployed | Export from the CI system or read-only access |
| CloudTrail (trail bucket or Event history) | Who created or changed what, and when | Read through the role above |

**Access rules.** The role trusts only my AWS account, requires an external ID, and has a session limit of one hour. It cannot read customer data: object reads, secrets, parameters, table items and log events are explicitly denied (the Terraform state object is the one exception, when drift is in scope). The client removes the role when the assessment ends. I do not ask for administrator credentials, console passwords or access keys.

## 3. Evidence hierarchy

| Level | Source | Examples | Can it verify a criterion? |
|---|---|---|---|
| **L1** | Observed in the running system | AWS API reads, CloudTrail events, CI/CD run history, a live endpoint | Yes |
| **L2** | Infrastructure as code: state and plan | `terraform state`, `terraform plan -refresh-only` drift | Supports only |
| **L3** | Repository and pipeline definitions | Commits on the delivered branch, tags, workflow files, merged pull requests | Supports only |
| **L4** | Documents | Architecture documents, runbooks, test records, screenshots | Supports only |
| **L5** | Claims | Status reports, invoices, meeting notes, statements | Never on its own |

**Conflict rules**
- When sources disagree, the higher level wins, and the criterion is marked **Contradicted**, not just Missing. Contradictions are reported because they show that a claim was wrong, not only incomplete.
- Delivered means *on the contracted branch, release or account*. Work on an unmerged branch or in a sandbox account is reported but scores as not delivered.
- An observation outside the record window (CloudTrail Event history keeps 90 days) cannot be L1. It can still be L4 if a document records it.

## 4. Procedure

1. **Decompose the SOW.** Each contracted task gets 2–5 acceptance criteria written as checkable statements ("RDS runs Multi-AZ in production"), and a weight taken from the SOW (line-item value or effort share; equal weights if the SOW has none, stated in the report). The client confirms the list.
2. **Collect account evidence.** Run the [evidence kit](evidence-kit/) with the read-only role. Output: one JSON file per check, a facts sheet, and a manifest with the SHA-256 of every file.
3. **Check IaC against reality.** `terraform plan -refresh-only` shows changes made outside Terraform since the last apply; a normal `terraform plan` shows whether the delivered code matches what runs. Both use `-lock=false` and never `apply`. Only resource addresses and attribute names are kept, never values.
4. **Read the delivery record.** Commits in the contract window, tags, CI/CD definitions, unmerged branches, CI run history, and CloudTrail events for creation and change of in-scope resources (who and when).
5. **Read the documents** and list every claim they make about the in-scope items.
6. **Judge each criterion** (section 5) and write the gap in one sentence with its evidence IDs.
7. **Vendor right of reply.** The draft findings go to the vendor through the client, with a fixed window (usually three business days). New evidence can change a status; statements alone cannot.
8. **Score, report, sign.** Compute task and project completion from the scoring file, rank the risks, write recommendations, sign.

## 5. Completion scoring rubric

### Per acceptance criterion

| Status | Code | Rule |
|---|---|---|
| **Verified** | V | Observed at L1 during the assessment, and consistent with the contract wording. |
| **Supported** | S | Present at L2–L4 on the delivered branch or in delivered documents, but not observed running (no access, no run yet, outside the record window). |
| **Missing** | M | No evidence of delivery at any level, or only on an unmerged branch or in a sandbox. |
| **Contradicted** | C | Higher-level evidence shows the opposite of a claim or of a lower-level artifact (e.g. the document says Multi-AZ, the account says single-AZ). |
| **Unable to assess** | U | I could not get the access or records needed. Listed in the scope limits; counts as not delivered in both numbers below. |

### Per task

| Task status | Rule |
|---|---|
| Complete, verified | Every criterion is V. |
| Complete, unverified | Every criterion is V or S, and at least one is S. |
| Partial | At least one criterion is V or S, and at least one is M, C or U. |
| Not started | No criterion is V or S. |
| Unable to assess | Every criterion is U. |

- **Task verified %** = V ÷ criteria.
- **Task supported %** = (V + S) ÷ criteria.

### Project level

Project completion is the SOW-weighted average of the task percentages, reported as a **range**:

- **Lower bound (verified):** Σ weight × task verified % ÷ Σ weight. What I saw working.
- **Upper bound (supported):** Σ weight × task supported % ÷ Σ weight. What I saw working, plus what is credible in code and documents but not proven running.

A range is more honest than one number: the gap between the two bounds is the amount of work that still has to be proven, and the client decides how to treat it. Criteria inside a task are weighted equally unless the SOW says otherwise. No partial credit is given for "almost" (an encrypted database that is not Multi-AZ fails the Multi-AZ criterion; it passes the encryption criterion).

**Worked example (sample report, task T7 CI/CD, weight 15):** pull-request builds V, staging deploys V, production deploy with approval S (defined, never run). Status: Complete, unverified. Verified 2/3 = 67%, supported 3/3 = 100%.

The arithmetic is done by `python evidence-kit/kit.py score <scoring.csv>`; the statuses in the CSV are my judgments.

## 6. AWS evidence catalogue

How typical SOW items are checked. All calls are reads; the evidence kit automates the starred ones.

| SOW item | L1 evidence (account) | L2/L3 evidence |
|---|---|---|
| VPC across AZs, private subnets, NAT per AZ | ★ `DescribeSubnets`, `DescribeRouteTables`, `DescribeNatGateways` | VPC module inputs, drift |
| EKS / ECS cluster | ★ `DescribeCluster` (version, endpoint access, logs), `DescribeNodegroup`, `DescribeClusters` | Cluster module, Helm/Kustomize in repo |
| RDS / Aurora Multi-AZ, backups, encryption | ★ `DescribeDBInstances`, `DescribeDBClusters` | `multi_az`, `backup_retention_period` in state |
| AWS Backup | ★ `ListBackupPlans`, `GetBackupPlan`, `ListBackupSelections`; restore jobs in CloudTrail | Backup module |
| IAM least privilege | ★ users, attached/inline/group policies, MFA, access key age and last use, OIDC providers and federated roles | IAM modules, SSO configuration |
| CloudTrail | ★ `DescribeTrails`, `GetTrailStatus` | Trail module |
| WAF | ★ `ListWebACLs`, `GetWebACL`, `GetWebACLForResource` per public ALB | WAF module, merged or not |
| Cost allocation tags | ★ tags on instances, volumes, databases, clusters, load balancers, buckets | `default_tags` in provider |
| Who built and changed it | ★ CloudTrail `LookupEvents` for create/modify/restore events (90 days) | Commit authors, pipeline identities |
| Well-Architected Review | ★ Well-Architected Tool workloads, risk counts and milestones, if the SOW includes a review | Review report |
| CI/CD deployments | CI run history (export), `AssumeRoleWithWebIdentity` events for the deploy role | Workflow files, environments, approvals |

**Well-Architected framing.** If the SOW includes a Well-Architected Review, I check that the workload exists in the Well-Architected Tool and that milestones match the claims. Either way, I use the six pillars to rank risks in the report; I do not turn them into a score.

## 7. Independence and conflict of interest

- Before starting, I confirm in writing that I have no commercial or personal relationship with the vendor and no stake in the outcome. If that changes, I stop and tell the client.
- Remediation is not part of the assessment. If the client later wants help fixing the gaps, that is a separate contract agreed after the final report is delivered, and the report is not changed by it.
- My fee does not depend on the result.

## 8. Human-led review

- Scripts in this repository only **collect** facts through read-only calls and do arithmetic on the scoring file. They do not decide whether anything is complete.
- I read the evidence for every criterion, assign every status, write every finding and sign the report. The report says which tools collected which evidence.
- The evidence kit contains no AI component. Every status and finding in the report is my judgment.

## 9. Client data

- Evidence holds configuration metadata, not customer data. Access key IDs are cut to their last four characters; Terraform drift keeps attribute names only.
- Terraform state is treated as secret: it is read in place for the drift check and never copied into the evidence bundle.
- Evidence and working files are kept only for the period agreed with the client and then deleted. The client receives the full evidence bundle with the report.

## 10. What this method cannot prove

- Quality beyond the acceptance criteria (code design, performance under load, security of the application code) unless those are written into the criteria and tested.
- Events older than the record window, unless the client has the CloudTrail trail bucket for that period and grants read access to it.
- Other accounts and regions than the ones in scope. Multi-account organisations need one run per account and region.
- Intent. The report says what the evidence shows; whether a gap is a breach of contract is for the client and its counsel.
