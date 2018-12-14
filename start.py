import ipywidgets as ipw

def get_start_widget(appbase, jupbase):
    #http://fontawesome.io/icons/
    template = """
    <table>
    <tr>

    <td style="text-align:center">
    <a style="{style}" href="{jupbase}/tree/" title="File Browser" target="_blank">
    <i class="fa fa-file-text-o fa-4x"></i>
    </a><br>
    File Browser
    </td>

    <td style="width:70px"></td>

    <td style="text-align:center">
    <a style="{style}" href="{appbase}/terminal.ipynb" title="Open Terminal" target="_blank">
    <i class="fa fa-terminal fa-4x"></i>
    </a><br>
    Terminal
    </td>

    <td style="width:70px"></td>

    <td style="text-align:center">
    <a style="{style}" href="{jupbase}/tree/#running" title="Task Manager" target="_blank">
    <i class="fa fa-tasks fa-4x"></i>
    </a><br>
    Tasks
    </td>

    <td style="width:70px"></td>

    <td style="text-align:center">
    <a style="{style}" href="{appbase}/appstore.ipynb" title="App Store">
    <i class="fa fa-cog fa-4x" aria-hidden="true"></i>
    </a><br>
    Manage Apps
    </td>
    </tr>
    </table>
"""
    
    html = template.format(appbase=appbase, jupbase=jupbase, style="margin:20px")
    return ipw.HTML(html)
    
#EOF
