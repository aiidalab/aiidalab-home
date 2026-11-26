import ipywidgets as ipw
from nbclassic import nbclassic_path


def get_start_widget(appbase, jupbase):
    # http://fontawesome.io/icons/

    # Notebook 7's presence moves the classic browser to /nbclassic, even when
    # its server extension is disabled. Keep jupbase for JupyterHub deployments.
    classicbase = jupbase + nbclassic_path()

    template = """
<nav id="home-navbar">
  <div id="nav-buttons">
    <div class="nav-item">
      <a href="{classicbase}/tree/" title="File Manager" target="_blank">
        <i class="fa fa-file-text-o fa-3x"></i>
      </a>
      <span>File Manager</span>
    </div>
    <div class="nav-item">
      <a href="{appbase}/terminal.ipynb" title="Open Terminal" target="_blank">
        <i class="fa fa-terminal fa-3x"></i>
      </a>
      <span>Terminal</span>
    </div>
    <div class="nav-item">
      <a
        href="{classicbase}/tree/#running"
        title="Task Manager"
        target="_blank"
      >
        <i class="fa fa-tasks fa-3x"></i>
      </a>
      <span>Tasks</span>
    </div>
    <div class="nav-item">
      <a
        href="{appbase}/appstore.ipynb"
        title="Install New Apps"
        target="_blank"
      >
        <i class="fa fa-puzzle-piece fa-3x"></i>
      </a>
      <span>App Store</span>
    </div>
    <div class="nav-item">
      <a
        href="{appbase}/control.ipynb"
        title="Manage AiiDA and AiiDAlab"
        target="_blank"
      >
        <i class="fa fa-cogs fa-3x"></i>
      </a>
      <span>Control</span>
    </div>
    <div class="nav-item">
      <a
        href="https://aiidalab.readthedocs.io"
        title="Learn about AiiDAlab"
        target="_blank"
      >
        <i class="fa fa-question fa-3x"></i>
      </a>
      <span>Help</span>
    </div>
  </div>
  <div id="extra-nav-links">
    <a
      href="{appbase}/process_list.ipynb"
      title="List Processes"
      target="_blank"
      >Process list</a
    >
    |
    <a
      href="{appbase}/code_setup.ipynb"
      title="Set up AiiDA codes"
      target="_blank"
      >Code setup</a
    >
  </div>
</nav>
"""

    html = template.format(appbase=appbase, classicbase=classicbase)
    links = ipw.HTML(html)
    try:
        from aiidalab_ai_assistant import AssistantPanel
    except ImportError:
        return links
    return ipw.VBox([AssistantPanel(apps_url="../"), links])
