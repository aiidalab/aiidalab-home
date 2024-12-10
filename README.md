# AiiDAlab - Home

The home app parses AiiDAlab apps present in `$AIIDALAB_HOME` and displays them
using their `start.py` or `start.md` files.

## Customization

The home app reads the following environment variables:

* `AIIDALAB_APPS` (default: `/project/apps`): Place apps in this folder
* `AIIDALAB_HOME` (default: `/project`): Place ssh credentials in this folder
* `AIIDALAB_SCRIPTS` (default: `/opt`): Currently unused

## Citation

Users of AiiDAlab are kindly asked to cite the following publication in their own work:

A. V. Yakutovich et al., Comp. Mat. Sci. 188, 110165 (2021).
[DOI:10.1016/j.commatsci.2020.110165](https://doi.org/10.1016/j.commatsci.2020.110165)

## For maintainers

To create a new release, clone the repository, install development dependencies with `pip install '.[dev]'`, and then execute `bumpver update`.
This will:

  1. Create a tagged release with bumped version and push it to the repository.
  2. Trigger a GitHub actions workflow that creates a GitHub release.

Additional notes:

  - Use the `--dry` option to preview the release change.
  - The release tag (e.g. a/b/rc) is determined from the last release.
    Use the `--tag` option to switch the release tag.

## Acknowledgements

This work is supported by the [MARVEL National Centre for Competency in Research](<https://nccr-marvel.ch>)
funded by the [Swiss National Science Foundation](<https://www.snf.ch/en>), as well as by the [MaX
European Centre of Excellence](<https://www.max-centre.eu/>) funded by the Horizon 2020 EINFRA-5 program,
Grant No. 676598.

![MARVEL](miscellaneous/logos/MARVEL.png)
![MaX](miscellaneous/logos/MaX.png)
