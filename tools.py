from __future__ import print_function

# using dulwich
def git_repo_update_available(local_path, remote_url, repo_name, branch='master'):
    """Check whether there are new updates available at the remote repository."""
    from dulwich.errors import GitProtocolError
    from dulwich.client import HttpGitClient
    from dulwich.repo import Repo
    local_repo = Repo(local_path)
    remote_client = HttpGitClient(remote_url)

    # TODO: if local and remote repositories are different, error is not raised
    remote_refs = remote_client.fetch(repo_name, local_repo)

    # TODO: now it compares the ids only. Should also compare the time
    remote_id = remote_refs['refs/heads/'+branch]
    if local_repo['refs/heads/'+branch].id == remote_id:
        print("No update available")
    else:
        print("Update available")

