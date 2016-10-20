from functools import wraps

from fabric import tasks
from fabric.api import env, execute, hide, run, task, runs_once, settings
from fabric.context_managers import cd

SERVERS = {
    'transcript': {
        'host': 'archives-prod-transcript-app.nypr.digital',
        'environment': 'prod',
        'roles': ['transcript']
    },
}

REPO = 'git@github.com:nypublicradio/transcript-editor'

def _wrap_as_new(original, new):
    if isinstance(original, tasks.Task):
        return tasks.WrappedCallableTask(new)
    return new

def strict_roles(*role_list):
    """
    Extended version of the built-in fabric roles dectorator which will
    not run the task if the current host is not found in the roledefs
    for each role assigned to the decorated task.

    Work around for this: https://github.com/fabric/fabric/issues/464
    Original Gist: https://gist.github.com/Nagyman/2974290
    """
    def attach_list(func):
        @wraps(func)
        def inner_decorator(*args, **kwargs):
            # Check for the current host in the roledefs for
            # each role the task is restricted to.
            for role in getattr(func, 'roles', []):
                if env.host in env.roledefs.get(role, []):
                    return func(*args, **kwargs)
            return False
        _values = role_list
        # Allow for single iterable argument as well as *args
        if len(_values) == 1 and not isinstance(_values[0], basestring):
            _values = _values[0]
        setattr(inner_decorator, 'roles', list(_values))
        setattr(func, 'roles', list(_values))
        # Don't replace @task new-style task objects with inner_decorator by
        # itself -- wrap in a new Task object first.
        inner_decorator = _wrap_as_new(func, inner_decorator)
        return inner_decorator
    return attach_list

def _get_roledefs(environment):
    roledefs = {}
    roles = list(set([ role for traits in SERVERS.values() for role in traits['roles'] ]))
    for role in roles:
        roledefs[role] = [ traits['host'] for server, traits in SERVERS.items()
                            if role in traits['roles']
                            and (environment == traits['environment'] or traits['environment'] == 'all') ]
    return roledefs

def _load_shared_env_dict():
    env.user = 'transcript'
    env.git_dir = '/opt/transcript/transcript-editor'
    env.roledefs = _get_roledefs(env.environment)
    env.roles = [ role for role, hosts in env.roledefs.items() if len(hosts) > 0 ]
    env.git_branch = env.environment

### CLI Args

@task
@runs_once
def prod():
    """ fab prod deploy """
    env.environment = 'prod'
    _load_shared_env_dict()
    env.git_branch = 'master'

@task
@runs_once
def r(revision):
    """ fab <env> r:"<myrevision>" deploy """
    env.git_revision = revision

@task
@runs_once
def deploy(branch=None):
    """ fab <environment> m:"<mymyessage>" deploy:[branch] """
    execute(_check_git_dir)
    if not branch:
        branch = env.git_branch
    execute(_git_info, branch)
    if not getattr(env, 'git_revision', None):
        env.git_revision = env.git_info['revision']
    execute(_deploy_git, branch)
    execute(_install)
    execute(_restart)


### Git Tasks

@strict_roles('transcript')
def _check_git_dir():
    with settings(warn_only=True):
        git_dir_check = run('test -d {}'.format(env.git_dir))
    if git_dir_check.failed:
        with cd('~/'):
            run('git clone {}'.format(REPO))
    
@strict_roles('transcript')
def _deploy_git(branch):
    with cd(env.git_dir):
        run('git fetch -q origin +{0}:remotes/origin/{0}'.format(branch))
        run('git reset --hard remotes/origin/{0}'.format(branch))
        run('git checkout {0}'.format(env.git_revision))

@strict_roles('transcript')
def _git_info(branch):
    if hasattr(env, 'git_info'):
        return
    with cd(env.git_dir), hide('running','stdout'):
        run('git fetch -q origin +{0}:remotes/origin/{0}'.format(branch))
        revision = run('git rev-parse remotes/origin/{0}'.format(branch))
        changed_files = run('git diff --no-color --name-only remotes/origin/{0} | cat'.format(branch))
        previous_commit = run('git log --no-color -1 --full-history HEAD | cat')
        deployed_commit = run('git log --no-color -1 --full-history remotes/origin/{0} | cat'.format(branch))
    env.git_info = {
        'revision': str(revision),
        'previous_commit': str(previous_commit),
        'deployed_commit': str(deployed_commit),
        'changed_files': changed_files.split('\n')
    }

### Service Tasks

@strict_roles('transcript')
def _install():
    with cd(env.git_dir):
        run('bundle install --path vendor/bundle')
        run('test -e config/application.yml || ln -s /etc/transcript-editor/application.yml config/application.yml')
        run('test -e config/database.yml || ln -s /etc/transcript-editor/database.yml config/database.yml')
        run('RAILS_ENV=production rake db:version || RAILS_ENV=production rake db:setup')
        run('RAILS_ENV=production rake project:load["nypr-archives"]')

@strict_roles('transcript')
def _restart():
    run("sudo /usr/systemctl restart transcript")
