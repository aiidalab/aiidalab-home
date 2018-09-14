# AiiDA Lab - Home 

The home app parses apps present in `$AIIDALAB_HOME` (default: `/project`) and displays them
using their `start.py` or `start.md` files.

## Customization

The home app reads the following environment variables:

 * `AIIDALAB_APPS` (default: `/project/apps`): Place apps in this folder
 * `AIIDALAB_HOME` (default: `/project`): Place ssh credentials in this folder
 * `AIIDALAB_SCRIPTS` (default: `/opt`): Currently unused
