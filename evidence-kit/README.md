# Evidence kit

Small read-only scripts that collect the facts the [scoring rubric](../method.md#5-completion-scoring-rubric) needs. They do not judge: every status in a report is assigned by the reviewer.

| Command | What it collects | Reads from |
|---|---|---|
| `kit.py aws` | Checks E-00 to E-10 (table below) into one JSON file each, plus `summary.md` and `manifest.json` | AWS APIs, read-only role |
| `kit.py drift` | Terraform state vs. live account, and delivered code vs. live account | `terraform plan -refresh-only` / `terraform plan`, `-lock=false` |
| `kit.py repo` | Commits in the contract window, authors, tags, CI/CD files, IaC folders, unmerged branches | Local git clone |
| `kit.py score` | Task and project completion from the reviewer's scoring CSV | CSV file |
| `kit.py demo` | Runs `aws` against a fictional account simulated with moto (offline) | Memory only |

## AWS checks

| ID | Check | API calls (all reads) |
|---|---|---|
| E-00 | Account and caller | `sts:GetCallerIdentity` |
| E-01 | VPCs, subnets classified as public / private-nat / isolated by their route tables, NAT gateways per AZ | `ec2:DescribeVpcs`, `DescribeSubnets`, `DescribeRouteTables`, `DescribeNatGateways` |
| E-02 | EKS version, endpoint access and CIDRs, control plane logs, node group AZs; ECS clusters | `eks:ListClusters`, `DescribeCluster`, `ListNodegroups`, `DescribeNodegroup`, `ecs:ListClusters`, `DescribeClusters` |
| E-03 | RDS Multi-AZ, backup retention, encryption, public access, deletion protection; Aurora clusters | `rds:DescribeDBInstances`, `DescribeDBClusters` |
| E-04 | AWS Backup plans, rules, retention, selections | `backup:ListBackupPlans`, `GetBackupPlan`, `ListBackupSelections` |
| E-05 | IAM users with admin rights (direct, inline or via group), console login, MFA, access key age and last use; OIDC providers and the roles that trust them | `iam:List*`, `GetPolicy`, `GetPolicyVersion`, `GetUserPolicy`, `GetLoginProfile`, `GetAccessKeyLastUsed` |
| E-06 | CloudTrail trails: logging, multi-region, log file validation, bucket | `cloudtrail:DescribeTrails`, `GetTrailStatus` |
| E-07 | WAF web ACLs and managed rule groups; which internet-facing ALBs have a web ACL | `wafv2:ListWebACLs`, `GetWebACL`, `GetWebACLForResource`, `elbv2:DescribeLoadBalancers` |
| E-08 | Required tags (default `env,owner,cost-center`) on instances, volumes, databases, EKS clusters, load balancers, buckets | `Describe*`, `elbv2:DescribeTags`, `s3:ListBuckets`, `GetBucketTagging` |
| E-09 | Who created or changed in-scope resources, from CloudTrail Event history (max 90 days) | `cloudtrail:LookupEvents` |
| E-10 | Well-Architected Tool workloads, risk counts, milestones | `wellarchitected:ListWorkloads`, `ListMilestones` |

## How it stays read-only

1. **IAM first.** The client creates a role from [`iam/reviewer-trust-policy.json`](iam/reviewer-trust-policy.json) (external ID and MFA required) with the AWS managed policies `SecurityAudit` and `ReadOnlyAccess`, plus [`iam/deny-data-reads.json`](iam/deny-data-reads.json) as an inline policy. `ReadOnlyAccess` alone would allow reading S3 objects and some data-plane APIs; the deny removes them. Replace the placeholders before use. If drift is out of scope, point the state ARN at a key that does not exist.
2. **Guard in code.** [`evidence_kit/guard.py`](evidence_kit/guard.py) hooks every boto3 call *before the request is built*. Only `Describe*`, `List*`, `Get*`, `Lookup*` and `Search*` operations pass, and data reads such as `GetObject`, `GetSecretValue` and `GetParameter` are blocked even though they are reads. A blocked call raises `ReadOnlyViolation` and nothing is sent.
3. **Terraform and git wrappers refuse writes.** `drift.py` only runs `init`, `plan`, `show` and `version`, refuses `-auto-approve` and `-destroy`, and requires `-lock=false` on every plan. `repo.py` only runs `log`, `rev-parse`, `for-each-ref`, `ls-tree`, `show` and `branch` in list mode.
4. **Errors are not facts.** A call that fails (permission, service not enabled) is written as `NOT COLLECTED` with the error. A check that collected nothing never prints "none found".
5. **No secrets in the bundle.** Access key IDs are cut to the last four characters. Drift output keeps resource addresses and attribute *names*, never values, because state can contain passwords.

## Usage with a real account

```bash
# ~/.aws/config, profile that assumes the client's reviewer role
# [profile acme-review]
# role_arn = arn:aws:iam::<client-account>:role/delivery-review-readonly
# source_profile = reviewer
# external_id = <agreed external id>
# mfa_serial = arn:aws:iam::<reviewer-account>:mfa/<device>
# duration_seconds = 3600

python evidence-kit/kit.py aws --profile acme-review --region us-east-1 --out evidence/aws-us-east-1
python evidence-kit/kit.py drift --workdir ../client-infra/envs/prod --out evidence/T-01-drift.json
python evidence-kit/kit.py repo --path ../client-platform --branch main --since 2026-03-02 --until 2026-09-30 --out evidence/R-01-repo.json
```

Run `aws` once per region in scope. Hash the whole `evidence/` folder at the end (`find evidence -type f ! -name SHA256SUMS -exec sha256sum {} + > evidence/SHA256SUMS`) and send it with the report.

## Terraform drift: what the two plans mean

| Plan | Exit 0 | Exit 2 | What it answers |
|---|---|---|---|
| `terraform plan -refresh-only -detailed-exitcode -lock=false` | State matches the account | `resource_drift` lists resources changed **outside Terraform** since the last apply | "Did someone click in the console after delivery?" |
| `terraform plan -detailed-exitcode -lock=false` | Delivered code matches the account | `resource_changes` lists what the delivered code would create, change or destroy | "Is the code in the repo what is actually running?" |

Exit code 1 is an error and is reported as such (often a data source the read-only role is denied). The plan file is written to a temporary folder and deleted. `-lock=false` is required because a read-only role cannot write a state lock, and must not.

## Tests

`pytest` from the repo root. AWS calls are simulated with [moto](https://github.com/getmoto/moto) (fictional account, same seed as the demo) and with `botocore.stub.Stubber` for APIs moto does not implement (CloudTrail `LookupEvents`, Backup selections, Well-Architected Tool, WAF managed rules). The drift parser is tested on a JSON plan fixture and, when `terraform` is on the PATH, on a real plan of a built-in `terraform_data` resource (no provider download, no cloud).
