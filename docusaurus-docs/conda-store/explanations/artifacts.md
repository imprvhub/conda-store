---
description: Understand environment artifacts generated by conda-store
---

# Artifacts

conda environments can be created in a few different ways.
conda-store creates "artifacts" (corresponding to different environment creation options) for every environment, that can be shared with colleagues and used to reproduce environments.
In the conda-store UI, these are available in the **"Logs and Artifacts"** section
at the end of the environment page.

The following sections describe the various artifacts generated and how to create environments with them.

Environments in shared namespaces on conda-store can be accessed by everyone with access to that namespace, in which case you may not need to share the artifacts manually.
Artifacts are used to share your environment with external collaborators who don't have access to conda-store.

:::note
The libraries (conda, conda-lock, conda-pack, etc.) mentioned in the following sections are separate projects in the conda ecosystem. The environments created using them are not managed by conda-store.
:::

## YAML file (pinned)

YAML files that follow the conda specification are a common way to create environments.
conda-store creates a "pinned" YAML, where all the exact versions of requested packages (including `pip` packages) as well as all their dependencies are specified, to ensure new environments created match the original environment as closely as possible.

:::info
In rare cases, building environments from "pinned" YAML files may not solve because packages are routinely marked as broken and removed at the repository level.

**conda-forge** (default channel in conda-store)
has a [policy that packages are never removed but are marked as
broken][conda-forge-immutability-policy].
Most other channels do not have such a policy.
:::

Click on **"Show yml file"** link in the conda-store UI to open the file in a new browser tab. You can copy-and-past this file in [conda-store UI's YAML editor][cs-ui-yaml] to create a new environment managed by conda-store in a different namespace.

You can download the file and share with someone or use it to create an environment on a different machine. Assuming `conda` is installed, run the [CLI commands mentioned in the conda-documentation][conda-docs-create-env] with the corresponding filename to create a conda environment (on any machine).

## Lockfile

A conda lockfile is a representation of all (`conda` and `pip`) dependencies in
a given environment.
conda-store creates lockfiles using the [conda-lock][conda-lock-github] project.

Click on **"Show lockfile"** to open the lockfile in a new browser tab.
You can download the file and share with someone or use it to create an environment in a different space.

To create an environment att the new location, follow the [commands in the conda-lock documentation][conda-lock-install-env].

## Tarballs or archives

:::warning
Building environments from archives is only supported on Linux machines
because the tarballs are built on Linux machines.
:::

A tarball or archive is a _packaged_ environment that can be moved, unpacked, and used in a different location or on a different machine.

conda-store uses [Conda-Pack][conda-pack], a library for
creating tarballs of conda environments.

Click **"Download archive"** button to download the archive of your conda environment, and share/move it to the desired location.

To install the tarball, follow the [instructions for the target machine in the conda-pack documentation][conda-pack-usage].

## Docker images

:::warning
Docker image creation is currently not supported.
:::

### Authentication

The `conda-store` docker registry requires authentication.
You can use **any username** and your **user token as the password**.

```bash
docker login -u <any-username> -p <conda-store-token>
```

To get your user token:

1. Visit your user page at `<your-conda-store-domain>/admin/user`
2. Click on "Create token", which displays your token
3. Click on "copy" to copy the token to your clipboard

Alternatively, you can set `c.AuthenticationBackend.predefined_tokens` in `conda_store_config.py`, which have environment read permissions on the given docker images required for pulling images.

### General usage

To use a specific environment build, click on the **"Show Docker image"** to get the URL to the docker image. For example: `localhost:8080/analyst/python-numpy-env:583dd55140491c6b4cfa46e36c203e10280fe7e180190aa28c13f6fc35702f8f-20210825-180211-244815-3-python-numpy-env`.

The URL consists of: `<conda-store-domain>/<namespace>/<environment-name>:<build_key>`

* The conda-store domain (for example `localhost:8080/`) at the beginning tells Docker where the docker registry is located. Otherwise, Docker will try to use Docker Hub by default.
* The `<namespace>/<environment-name>` refers to the specific conda environment
* The "build key" is a combination of `<specification-sha256>-<build
date>-<build id>-<environment name>` which points to specific build of the environment. For example, a past version of the environment.

To use a conda-store environment docker image:

```bash
docker run -it <docker-url>
```

### On-demand (dynamic) docker image

In conda-store, you can also specify the required packages within the docker image name itself, without needing an actual environment to be created by conda-store UI.

The URL format is: `<registry-url>:<registry-port>/conda-store-dynamic/<package-constraint-1>/.../<package-constraint-n>`.

After `conda-store-dynamic`, you can specify packages with constraints separated by
slashes in the following format:
* `<=1.10` as `.lt.1.10`
* `>=1.10` as `.gt.1.10`

For example, if you need Python less than `3.10` and NumPy
greater than `1.0`, this would be the docker image
name: `<registry-url>:<registry-port>/conda-store-dynamic/python.lt.3.10/numpy.gt.1.0`.

conda-store creates the environment ands builds the docker image, which you can then download.

## Installers

Installers are another way to share and use a set of (bundled) packages.
conda-store uses [constructor][constructor-docs] to generate an installer for the current platform (where the server is running):

- on Linux and MacOS, it generates a `.sh` installer
- on Windows, it generates a `.exe` installer using NSIS

conda-store automatically adds `conda` and `pip` to the target environment
because these are required for the installer to work.

:::note
`constructor` uses a separate dependency solver instead of
utilizing the generated lockfile, so the package versions used by the installer
might be different compared to the environment available in conda-store. There
are plans to address this issue in the future.
:::

<!-- Internal links -->
[cs-ui-yaml]: ../../conda-store-ui/tutorials/create-envs#yaml-editor

<!-- External links -->
[conda-docs]: https://docs.conda.io/projects/conda/en/latest/user-guide/concepts/environments.html
[conda-forge-immutability-policy]: https://conda-forge.org/docs/maintainer/updating_pkgs.html#packages-on-conda-forge-are-immutable
[conda-docs-create-env]: https://conda.io/projects/conda/en/latest/user-guide/tasks/manage-environments.html#creating-an-environment-from-an-environment-yml-file
[conda-lock-github]: https://github.com/conda-incubator/conda-lock
[conda-lock-install-env]: https://conda.github.io/conda-lock/output/#environment-lockfile
[constructor]: https://github.com/conda/constructor
[conda-pack]: https://conda.github.io/conda-pack/
[conda-pack-usage]: https://conda.github.io/conda-pack/index.html#commandline-usage
[conda-docker]: https://github.com/conda-incubator/conda-docker
[constructor-docs]: https://conda.github.io/constructor/
