#!/usr/bin/python
#
# (c) Copyright 2018 SUSE LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
#

DOCUMENTATION = '''
---
module: zypper_check_repositories
short_description: Compare installed zypper repositories to required repos
description: |
  Check the zypper repositories installed on the system and compare them with
  a dictionary that describes the metadata of required repositories. Fails if
  all required repositories are not installed on the system.
author: SUSE Linux GmbH
options:
  expected_repodata:
    description: Dictionary describing the repomd of required repositories.
    default: {}
'''

EXAMPLES = '''
- set_fact:
    expected_repodata:
      cloud:
        name: "Cloud"
        repomd:
          tags:
            - "obsproduct://build.suse.de/SUSE:SLE-12-SP3:Update:Products:Cloud8/suse-openstack-cloud/8/cd/x86_64"
            - "obsproduct://build.suse.de/Devel:Cloud:8:Staging/suse-openstack-cloud/8/POOL/x86_64"
- name: osconfig | setup-zypp | Check that SLES and Cloud repos are installed
  zypper_check_repositories:
    expected_repodata: expected_repodata
    brand: "SUSE OpenStack Cloud"
'''

import glob
import re
import os
import urllib2
from xml.etree import ElementTree


try:
    import urlparse # python2
except ModuleNotFoundError:
    import urllib.parse as urlparse # python3


from ansible.module_utils.urls import * # Provides the open_url method


def _required_tags(expected_repodata, brand):
    tags = {repo['name']: repo['repomd']['tags']
            for repo in expected_repodata.values()
            if 'brand' not in repo or repo['brand'] == brand}
    return tags


def _repomd_sources():
    repomd_sources = []
    for repo in glob.glob('/etc/zypp/repos.d/*.repo'):
        with open(repo) as f:
            baseurl = [line.split('=')[1].strip() for line in f.readlines()
                       if re.search(r'^baseurl=.*$', line)][0]
            url_parts = urlparse.urlparse(baseurl)
            if url_parts.scheme not in ('http', 'https', 'file', 'dir'):
                # NOTE(colleen): There are many posible URI formats that zypper
                # could potentially be using as a repository source. We don't
                # support them all right now.
                # See: https://en.opensuse.org/openSUSE:Libzypp_URIs
                continue
            repomd_source = url_parts._replace(
                path=urlparse.urljoin(
                    url_parts.path + '/', 'repodata/repomd.xml')).geturl()
            repomd_sources.append(repomd_source)
    return repomd_sources


def _fetch_url(url):
    resp = open_url(url)
    if resp.code and resp.code != 200:
        raise Exception("Could not reach remote repository: %s" % url)
    return resp


def _fetch_repomd(repo_uri):
    url = re.sub(r'^dir:', 'file:', repo_uri)
    try:
        repomd = _fetch_url(url).read()
    except urllib2.URLError:
        # ISO media might be under suse/
        url = url.replace('repodata', 'suse/repodata')
        try:
            repomd = _fetch_url(url).read()
        except urllib2.URLError:
            # This is not a repomd-format repo, move on
           return
    return repomd


def _find_repotags():
    found_repotags = {}
    repomd_sources = _repomd_sources()
    for repomd_source in repomd_sources:
        repomd = _fetch_repomd(repomd_source)
        if not repomd:
            continue

        xmlns = {'repo': 'http://linux.duke.edu/metadata/repo'}
        try:
            xmlroot = ElementTree.fromstring(repomd)
            taglist = xmlroot.findall('repo:tags', xmlns)
            for repotag in taglist:
                repos = repotag.findall('repo:repo', xmlns)
                for r in repos:
                    found_repotags[r.text] = 1
        except Exception as e:
            raise Exception("Could not understand repo metadata located at "
                            "%s: %s" % (repomd_source, e.message))
    return found_repotags


def run(expected_repodata, brand):

    found_repotags = _find_repotags()

    unfound_repos = []
    for name, tags in _required_tags(expected_repodata, brand).items():
        found_tag = False
        for tag in tags:
            if tag in found_repotags:
                found_tag = True
        if not found_tag:
            unfound_repos.append(name)
    if any(unfound_repos):
        raise Exception("Missing required repositories: %s" % ', '.join(
            unfound_repos))


def main():

    argument_spec = dict(
        expected_repodata=dict(default=dict()),
        brand=dict(type='str', required=True),
    )
    module = AnsibleModule(argument_spec=argument_spec,
                           supports_check_mode=False)
    params = module.params

    try:
        run(params['expected_repodata'], params['brand'])
    except Exception as e:
        module.fail_json(msg=e.message)
    module.exit_json(rc=0, changed=False,
                     msg="All required repositories are installed.")


from ansible.module_utils.basic import *


if __name__ == '__main__':
    main()
