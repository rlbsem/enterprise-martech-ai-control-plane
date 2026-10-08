# Static analysis inputs

These deliberately fake identifiers exist only to make configuration evaluation concrete during security scanning. They are not Terraform deployment inputs, AWS evidence or usable credentials. The `example.test` domain, repeated `a` image/commit hashes and `123456789012` account ID identify the fixture. Native Terraform tests have their own equivalent mock-provider inputs.
