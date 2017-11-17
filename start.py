import ipywidgets as ipw

def get_start_widget(appbase, jupbase):
    template = """
<a style="{style}" href="{jupbase}/tree/" title="File Browser" target="_blank">
<i class="fa fa-file-text-o fa-4x"></i>
</a>

<a style="{style}" href="{appbase}/terminal.ipynb" title="Open Terminal" target="_blank">
<i class="fa fa-terminal fa-4x"></i>
</a>

<a style="{style}" href="{jupbase}/tree/#running" title="Task Manager" target="_blank">
<i class="fa fa-tasks fa-4x"></i>
</a>

<a style="{style}" href="{appbase}/appmanager.ipynb" title="Manage Apps">
<i class="fa fa-cog fa-4x" aria-hidden="true"></i>
</a>
"""
    
    html = template.format(appbase=appbase, jupbase=jupbase, style="margin:20px")
    return ipw.HTML(html)
    
#EOF
