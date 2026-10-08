# PostgreSQL trust store

`rds-global-bundle.pem` is the public AWS RDS CA bundle obtained from [the AWS trust store](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem). It contains public certificates, no private keys. Cloud database clients use `sslmode=verify-full` and this file; plaintext or hostname-unverified database sessions are not configured.

The main verification manifest binds the exact bytes (LF normalized). Review AWS certificate notices, refresh from the authoritative source and rerun validation before the applicable CA expiry. This bundle serves RDS only; the HTTPS ALB certificate must be trusted by the worker's normal public CA store as described in the infrastructure prerequisites.
