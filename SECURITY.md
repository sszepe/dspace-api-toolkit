# Security

## Handling credentials

The toolkit never logs passwords or tokens. Keep credentials in environment
variables or an uncommitted `.env` file; the integration tests use only the
throw-away admin account of the local Docker stacks.