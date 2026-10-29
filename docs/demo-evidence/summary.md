# Evidence summary

- Collected by: evidence-kit (read-only guard on), mode: **demo (moto, fictional account)**
- Region: us-east-1 · Started: 2026-10-29 10:46:43.962014+00:00
- This sheet lists facts only. Statuses are assigned by the reviewer using the rubric in `method.md`.

| Evidence | Fact | Observed value | Reviewer notes |
|---|---|---|---|
| E-00 | Account / region | 123456789012 / us-east-1 | |
| E-00 | Caller | arn:aws:sts::123456789012:user/moto | |
| E-01 | VPC vpc-ef7d6f55c35a95493 (default) | AZs: us-east-1a, us-east-1b, us-east-1c, us-east-1d, us-east-1e, us-east-1f; subnets: 6 isolated | |
| E-01 | VPC vpc-ef7d6f55c35a95493 (default) NAT gateways | 0 (AZs: none) | |
| E-01 | VPC fieldwork-prod | AZs: us-east-1a, us-east-1b, us-east-1c; subnets: 3 private-nat, 3 public | |
| E-01 | VPC fieldwork-prod NAT gateways | 1 (AZs: us-east-1a) | |
| E-02 | EKS fieldwork-prod | version 1.30, status ACTIVE; public endpoint yes (CIDRs 0.0.0.0/0); private endpoint no | |
| E-02 | EKS fieldwork-prod logs | api, audit, authenticator | |
| E-02 | EKS fieldwork-prod node group general | AZs: us-east-1a, us-east-1b, us-east-1c | |
| E-03 | RDS fieldwork-prod-db (postgres) | Multi-AZ no; backup retention 7 d; encrypted yes; public no; deletion protection yes | |
| E-04 (partial) | Backup plan fieldwork-daily | daily-35d cron(0 5 * * ? *) keep 35 d; selections: not readable | |
| E-04 (partial) | NOT COLLECTED: backup:ListBackupSelections 51325280-c40a-411f-8421-6bd41695c896 | NotImplementedError: The list_backup_selections action has not been implemented | |
| E-05 | IAM user vendor-deploy | admin yes (attached:AdministratorAccess); console no; MFA devices 0; access keys: …3THY Active | |
| E-05 | IAM user admin-temp | admin yes (group:admins:AdministratorAccess); console yes; MFA devices 0; access keys: none | |
| E-05 | IAM user readonly-auditor | admin no; console no; MFA devices 0; access keys: none | |
| E-05 | OIDC providers | token.actions.githubusercontent.com, oidc.eks.us-east-1.amazonaws.com/id/FS7FVzcgSo | |
| E-05 | Role github-deploy-staging | trusts token.actions.githubusercontent.com | |
| E-05 | Role fieldwork-api-irsa | trusts oidc.eks.us-east-1.amazonaws.com/id/FS7FVzcgSo | |
| E-06 | Trail acme-account-trail | logging yes; multi-region yes; log file validation yes; bucket acme-fieldwork-cloudtrail-logs | |
| E-07 | Regional web ACLs | none | |
| E-07 | Public ALB fieldwork-public | web ACL: none | |
| E-08 | Required tags | env, owner, cost-center | |
| E-08 | Resources checked | 4 | |
| E-08 | rds:db fieldwork-prod-db | missing: cost-center | |
| E-08 | elbv2:load-balancer fieldwork-public | missing: cost-center | |
| E-08 | s3:bucket acme-fieldwork-cloudtrail-logs | missing: env, owner, cost-center | |
| E-09 (not collected) | NOT COLLECTED: cloudtrail:LookupEvents CreateCluster and 13 similar calls | NotImplementedError: The lookup_events action has not been implemented | |
| E-10 (not collected) | NOT COLLECTED: wellarchitected:ListWorkloads | ClientError: An error occurred (404) when calling the ListWorkloads operation: Not yet implemented | |

> Demo note: moto does not simulate CloudTrail LookupEvents, AWS Backup selections or the Well-Architected Tool, so those calls show as NOT COLLECTED here. They are covered by botocore Stubber tests in tests/test_checks_stubber.py.
