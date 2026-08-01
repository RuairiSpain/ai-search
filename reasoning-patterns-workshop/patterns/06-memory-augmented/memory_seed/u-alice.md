# Environment profile — u-alice (Contoso corp customer)

- Product: Contoso Data Platform v4.2
- Deployment: Kubernetes on AKS, 3-node cluster
- Database: PostgreSQL 15, connection pool max 40
- Region: EU (Frankfurt)
- Known peculiarity: batch jobs run at 02:30 UTC nightly; scheduler host TZ
  differs from cluster TZ by 2 hours.
