# coding: utf-8
"""JupyterLab entry points"""

# Copyright (c) Jupyter Development Team.
# Distributed under the terms of the Modified BSD License.
import errno
import json
import pipes
import os
import glob
from uuid import uuid4
from os import path as osp
from os.path import join as pjoin
from subprocess import check_output, CalledProcessError
import shutil
import sys
import tarfile
from jupyter_core.paths import ENV_JUPYTER_PATH


if sys.platform == 'win32':
    from subprocess import list2cmdline
else:
    def list2cmdline(cmd_list):
        return ' '.join(map(pipes.quote, cmd_list))


here = osp.dirname(osp.abspath(__file__))


def get_app_dir(app_dir=None):
    """Get the configured JupyterLab app directory.
    """
    app_dir = app_dir or os.environ.get('JUPYTERLAB_DIR')
    app_dir = app_dir or pjoin(ENV_JUPYTER_PATH[0], 'lab')
    return os.path.realpath(app_dir)


def run(cmd, **kwargs):
    """Run a command in the given working directory.
    """
    print('> ' + list2cmdline(cmd))
    kwargs.setdefault('shell', sys.platform == 'win32')
    kwargs.setdefault('env', os.environ)
    try:
        return check_output(cmd, **kwargs)
    except CalledProcessError as error:
        raise error


def install_extension(extension, app_dir=None):
    """Install an extension package into JupyterLab.

    Follows the semantics of https://docs.npmjs.com/cli/install.

    The extension is first validated.

    If link is true, the source directory is linked using `npm link`.
    """
    app_dir = get_app_dir(app_dir)
    if app_dir == here:
        raise ValueError('Cannot install extensions in core app')
    extension = _normalize_path(extension)
    _ensure_package(app_dir)
    target = pjoin(app_dir, 'extensions')
    # npm pack the extension
    output = run(['npm', 'pack', extension], cwd=target)

    name = output.decode('utf8').splitlines()[-1]
    data = _read_package(pjoin(target, name))
    _validate_package(data, extension)

    _ensure_package(app_dir)
    staging = pjoin(app_dir, 'staging')
    run(['npm', 'install', pjoin(target, name)], cwd=staging)


def link_package(path, app_dir=None):
    """Link a package against the JupyterLab build.
    """
    app_dir = get_app_dir(app_dir)
    if app_dir == here:
        raise ValueError('Cannot link packages in core app')

    path = _normalize_path(path)
    _ensure_package(app_dir)

    # Verify the package.json data.
    pkg_path = osp.join(path, 'package.json')
    if not osp.exists(pkg_path):
        msg = 'Linked package must point to a directory with package.json'
        raise ValueError(msg)

    with open(pkg_path) as fid:
        data = json.load(fid)

    is_extension = _is_extension(data)
    if is_extension:
        install_extension(path, app_dir)
    else:
        msg = ('*** Note: Linking non-extension package "%s" (lacks ' +
               '`jupyterlab.extension` metadata)')
        print(msg % data['name'])
    config = _get_build_config(app_dir)
    config.setdefault('linked_packages', dict())
    config['linked_packages'][data['name']] = path
    _write_build_config(config, app_dir)


def unlink_package(package, app_dir=None):
    """Unlink a package from JupyterLab by path or name.
    """
    package = _normalize_path(package)
    name = None
    app_dir = get_app_dir(app_dir)
    if app_dir == here:
        raise ValueError('Cannot link packages in core app')

    config = _get_build_config(app_dir)
    linked = config.setdefault('linked_packages', dict())
    for (key, value) in linked.items():
        if value == package or key == package:
            name = key
            break

    if not name:
        print('No package matching "%s" is linked' % package)
        return False

    del linked[name]
    config['linked_packages'] = linked
    _write_build_config(config, app_dir)

    extensions = _get_extensions(app_dir)
    if name in extensions:
        uninstall_extension(name, app_dir)

    return True


def _get_build_config(app_dir):
    """Get the build config data for the given app dir
    """
    target = pjoin(app_dir, 'settings', 'build_config.json')
    if not os.path.exists(target):
        return {}
    else:
        with open(target) as fid:
            return json.load(fid)


def _write_build_config(config, app_dir):
    """Write the build config to the app dir.
    """
    _ensure_package(app_dir)
    target = pjoin(app_dir, 'settings', 'build_config.json')
    with open(target, 'w') as fid:
        json.dump(config, fid, indent=4)


def uninstall_extension(name, app_dir=None):
    """Uninstall an extension by name.
    """
    app_dir = get_app_dir(app_dir)
    if app_dir == here:
        raise ValueError('Cannot install packages in core app')
    # Allow for uninstalled core extensions here.
    with open(pjoin(here, 'package.json')) as fid:
        data = json.load(fid)
        if name in data['jupyterlab']['extensions']:
            print('Uninstalling core extension %s' % name)
            config = _get_build_config(app_dir)
            uninstalled = config.get('uninstalled_core_extensions', [])
            if name not in uninstalled:
                uninstalled.append(name)
                config['uninstalled_core_extensions'] = uninstalled
                _write_build_config(config, app_dir)
            return True

    for (extname, path) in _get_extensions(app_dir).items():
        if extname == name:
            print('Uninstalling %s from %s' % (name, os.path.dirname(path)))
            os.remove(path)
            return True

    print('No labextension named "%s" installed' % name)
    return False


def list_extensions(app_dir=None):
    """List installed extensions.
    """
    app_dir = get_app_dir(app_dir)
    return sorted(_get_extensions(app_dir).keys())


def clean(app_dir=None):
    """Clean the JupyterLab application directory."""
    app_dir = get_app_dir(app_dir)
    if app_dir == here:
        raise ValueError('Cannot clean the core app')
    for name in ['static', 'staging']:
        target = pjoin(app_dir, name)
        if osp.exists(target):
            shutil.rmtree(target)


def build(app_dir=None, name=None, version=None, publicPath=None):
    """Build the JupyterLab application."""
    # Set up the build directory.
    app_dir = get_app_dir(app_dir)
    if app_dir == here:
        raise ValueError('Cannot build extensions in the core app')

    _ensure_package(app_dir, name, version, publicPath)
    staging = pjoin(app_dir, 'staging')

    # Make sure packages are installed.
    run(['npm', 'install'], cwd=staging)

    # Install the linked extensions.
    for path in _get_linked_packages(app_dir).values():
        run(['npm', 'install', path], cwd=staging)

    # Build the app.
    run(['npm', 'run', 'build'], cwd=staging)

    # Move the app to the static dir.
    static = pjoin(app_dir, 'static')
    if os.path.exists(static):
        shutil.rmtree(static)
    shutil.copytree(pjoin(staging, 'build'), static)


def _ensure_package(app_dir, name='JupyterLab', version=None, publicPath=None):
    """Make sure the build dir is set up.
    """
    if not os.path.exists(pjoin(app_dir, 'extensions')):
        try:
            os.makedirs(pjoin(app_dir, 'extensions'))
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

    settings = pjoin(app_dir, 'settings')
    if not os.path.exists(settings):
        try:
            os.makedirs(settings)
        except OSError as e:
            if e.errno != errno.EEXIST:
                raise

    staging = pjoin(app_dir, 'staging')
    if not os.path.exists(staging):
        os.makedirs(staging)

    for name in ['index.template.js', 'webpack.config.js']:
        dest = pjoin(staging, name)
        shutil.copy2(pjoin(here, name), dest)

    # Template the package.json file.
    pkg_path = pjoin(here, 'package.template.json')
    with open(pkg_path) as fid:
        data = json.load(fid)

    extensions = _get_extensions(app_dir)

    for (key, value) in extensions.items():
        data['dependencies'][key] = value
        data['jupyterlab']['extensions'].append(key)

    config = _get_build_config(app_dir)
    for item in config.get('uninstalled_core_extensions', []):
        data['jupyterlab']['extensions'].remove(item)

    data['jupyterlab']['name'] = name
    if version:
        data['jupyterlab']['version'] = version

    publicPath = publicPath or uuid4().hex
    if not publicPath.endswith('/'):
        publicPath += '/'
    data['jupyterlab']['publicPath'] = publicPath
    data['scripts']['build'] = 'webpack'

    pkg_path = pjoin(staging, 'package.json')
    with open(pkg_path, 'w') as fid:
        json.dump(data, fid, indent=4)


def _is_extension(data):
    """Detect if a package is an extension using its metadata.
    """
    if 'jupyterlab' not in data:
        return False
    if not isinstance(data['jupyterlab'], dict):
        return False
    return data['jupyterlab'].get('extension', False)


def _validate_package(data, extension):
    """Validate package.json data.
    """
    msg = '%s is not a valid JupyterLab extension' % extension
    if not _is_extension(data):
        raise ValueError(msg)


def _get_extensions(app_dir):
    """Get the extensions in a given app dir.
    """
    extensions = dict()

    # Look in sys_prefix and app_dir if different
    sys_path = pjoin(ENV_JUPYTER_PATH[0], 'lab', 'extensions')
    for target in glob.glob(pjoin(sys_path, '*.tgz')):
        data = _read_package(target)
        extensions[data['name']] = os.path.realpath(target)

    app_path = pjoin(app_dir, 'extensions')
    if app_path == sys_path or not os.path.exists(app_path):
        return extensions

    for target in glob.glob(pjoin(app_path, '*.tgz')):
        data = _read_package(target)
        extensions[data['name']] = os.path.realpath(target)

    return extensions


def _get_linked_packages(app_dir=None):
    """Get the linked packages metadata.
    """
    app_dir = get_app_dir(app_dir)
    config = _get_build_config(app_dir)
    linked = config.get('linked_packages', dict())
    dead = []
    for (name, path) in linked.items():
        if not os.path.exists(path):
            dead.append(name)

    if dead:
        extensions = _get_extensions(app_dir)

    for name in dead:
        path = linked[name]
        if name in extensions:
            uninstall_extension(name)
            print('**Note: Removing dead linked extension "%s"' % name)
        else:
            print('**Note: Removing dead linked package "%s"' % name)
        del linked[name]

    if dead:
        config['linked_packages'] = linked
        _write_build_config(config, app_dir)

    return config.get('linked_packages', dict())


def _read_package(target):
    """Read the package data in a given target tarball.
    """
    tar = tarfile.open(target, "r:gz")
    f = tar.extractfile('package/package.json')
    return json.loads(f.read().decode('utf8'))


def _normalize_path(extension):
    """Normalize a given extension if it is a path.
    """
    extension = osp.expanduser(extension)
    if osp.exists(extension):
        extension = osp.abspath(extension)
    return extension
