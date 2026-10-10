# Amfelt examples

Example datasets and hands-on tutorials for Amfelt. Each corpus is a self-contained
directory that ships its data, its documentation, and the notices it needs.

## Layout

| Directory    | Contents                                 | Status   |
| ------------ | ---------------------------------------- | -------- |
| `jaffle/`    | The Jaffle Shop dataset and its tutorial | Next     |
| `benchmark/` | A dataset for throughput measurements    | Reserved |
| `pii/`       | A dataset that contains personal data    | Reserved |

Only `jaffle/` is planned for the first version. The other directories are reserved names.

## Versioning

Each corpus is released on its own, with a tag of the form `<corpus>/vX.Y.Z`, for example
`jaffle/v1.0.0`.

- A change to the data or to the model is a **major** version.
- A change to comments or documentation is a **minor** version.

## License

This repository is licensed under the [Apache License 2.0](LICENSE). The Jaffle Shop data is
generated with jafgen, which is also Apache-2.0 licensed; its notice ships beside the data.

## Contributing

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/), checked
by commitlint. After cloning, run `pnpm install` to install the commit hook, and `pnpm lint`
to lint the Markdown.
