# Contributing

1. Read [docs/dev/architecture.md](docs/dev/architecture.md) and set up with [docs/dev/local-development.md](docs/dev/local-development.md).
2. Make the change with its test. A behaviour a user sees belongs in the story's test file.
3. Run the checks in [docs/dev/testing.md](docs/dev/testing.md).
4. If the change alters a design decision, add an ADR in `docs/adr/` and update the design document.
5. If it changes what a user sees, update the help in `help/` (DITA). The help gate fails if a screen has no help topic.
6. Add a line to [CHANGELOG.md](CHANGELOG.md) under Unreleased.

Versions follow [Semantic Versioning](https://semver.org/).
