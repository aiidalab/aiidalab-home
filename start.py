import ipywidgets as ipw


def get_start_widget(appbase, jupbase):
    # http://fontawesome.io/icons/

    width = "70px"

    template = """
    <center>
    <table>
    <tr>

    <td style="text-align:center">
    <a style="{style}" href="{jupbase}/tree/" title="File Manager" target="_blank">
    <i class="fa fa-file-text-o fa-3x"></i>
    </a><br>
    File Manager
    </td>

    <td style="width:{width}"></td>

    <td style="text-align:center">
    <a style="{style}" href="{appbase}/terminal.ipynb" title="Open Terminal" target="_blank">
    <i class="fa fa-terminal fa-3x"></i>
    </a><br>
    Terminal
    </td>

    <td style="width:{width}"></td>

    <td style="text-align:center">
    <a style="{style}" href="{jupbase}/tree/#running" title="Task Manager" target="_blank">
    <i class="fa fa-tasks fa-3x"></i>
    </a><br>
    Tasks
    </td>

    <td style="width:{width}"></td>

    <td style="text-align:center">
    <a style="{style}" href="{appbase}/appstore.ipynb" title="Install New Apps" target="_blank">
    <i class="fa fa-puzzle-piece fa-3x" aria-hidden="true"></i>
    </a><br>
    App Store
    </td>

    <td style="width:{width}"></td>

    <td style="text-align:center">
    <a style="{style}" href="{appbase}/control.ipynb" title="Manage AiiDA and AiiDAlab" target="_blank">
    <i class="fa fa-cogs fa-3x" aria-hidden="true"></i>
    </a><br>
    Control
    </td>

    <td style="width:{width}"></td>

    <td style="text-align:center">
    <a style="{style}" href="https://aiidalab.readthedocs.io" title="Learn about AiiDAlab" target="_blank">
    <i class="fa fa-question fa-3x" aria-hidden="true"></i>
    </a><br>
    Help
    </td>
    </tr>
    </table>
    </center>
"""

    html = template.format(
        appbase=appbase, jupbase=jupbase, style="margin:20px", width=width
    )
    return ipw.HTML(html)
