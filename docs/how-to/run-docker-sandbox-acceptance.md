# Run the Docker sandbox with deny_all and credential scoping

This profile runs a persistent Docker agent environment with loopback
networking and image-defined process variables. Use a trusted image
and dedicate the `agent-<agent_id>` volume to that agent.

```dotenv
AGENT_ENV_BACKEND=docker
AGENT_ENV_NETWORK_POLICY=deny_all
AGENT_ENV_CREDENTIAL_SCOPING=true
```

With an empty egress allow-list, the container uses Docker's `none`
network mode. Docker exec takes its process environment from the
container image. Host variables are not forwarded. When credential
scoping is enabled, containers with additional startup variables are
replaced before use. Keep credentials out of the image and the agent
volume. This profile covers commands sent through `DockerEnvironment`.

## Switch an existing deployment

1. Drain or pause tasks that use the environment and let already
   dispatched commands finish. Apply the configuration and restart
   the platform process.
2. Acquire the environment. The manager retires cached or warm Docker
   environments when their configuration changes. After a restart,
   Docker inspection verifies the named volume, image and applicable
   policy before reusing a container. An identified stale agent
   container is replaced. Incomplete inspection and foreign volumes
   cause acquisition to fail.
3. Verify that `docker inspect` shows `HostConfig.NetworkMode` equal to
   `none` and that it is the only network attachment. Check that the
   former `/env` data is readable. Run the acceptance tests before
   sending work to the environment.

Replacement preserves the `agent-<agent_id>` volume. Legacy
`hecate-internal-*` networks are outside the new profile's network
selection. Review their ownership and attachments before removing a
specific unused network. To return to the previous network mode, drain
the tasks, restore the settings, restart and verify the new container.

## Run acceptance

Use a reachable Linux Docker daemon with the `python:3.12-slim` image.
The daemon tests skip when either is missing. Record the daemon version
and image ID alongside the test output.

```bash
docker info --format '{{.OperatingSystem}} / {{.OSType}} / {{.ServerVersion}}'
docker image inspect python:3.12-slim --format '{{.Id}}'
bash scripts/dev.sh test tests/test_services/test_environment/ tests/test_runtime/test_sandbox_router.py tests/test_runtime/test_sandbox_enforcement_integration.py -q -rs
bash scripts/dev.sh test tests/test_services/test_environment/test_docker_environment_acceptance.py -v -s
bash scripts/dev.sh check all
```

The daemon tests use a separate receiver on a user-defined bridge. Hostname
and direct-IP probes reach it before policy tightening. The same probes
run from the replacement container and the receiver counts stay unchanged.
The tests also check marked and unmarked synthetic host credentials,
ordinary commands, file access, volume retention and same-name legacy
network replacement.

Tests remove their own uniquely named containers, test volumes and bridge
networks in teardown. After an interrupted run, identify the individual
test resources by name and ID before removing them.

See [Security architecture](../design/security-architecture.md) for the
limits of this profile and the separate tool execution controls.
