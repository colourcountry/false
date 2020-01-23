#!/usr/bin/python3

import os

class Config:
    def __init__(self,
               src_dir,
               url_base,
               output_dir,
               template_dir,
               home_site,
               id_base):
        self.src_dir = src_dir
        self.url_base = url_base
        if output_dir is None:
            self.output_dir = None
        else:
            self.output_dir = os.path.abspath(output_dir)
        if template_dir is None:
            self.template_dir = None
        else:
            self.template_dir = os.path.abspath(template_dir)
        self.home_site = home_site
        self.id_base = id_base

    def setIPFS(self, ipfs_module, ipfs_client, ipfs_namespace, ipfs_dir, ipfs_cache_dir=None):
        self.ipfs_module = ipfs_module
        self.ipfs_client = ipfs_client
        self.ipfs_namespace = ipfs_namespace
        if self.output_dir is None:
            self.ipfs_dir = None
        else:
            self.ipfs_dir = os.path.join(self.output_dir, ipfs_dir)
        if ipfs_cache_dir is None: 
            self.ipfs_cache_dir = None
        else:
            self.ipfs_cache_dir = os.path.abspath(ipfs_cache_dir)

    def validate(self):
        pass # TODO
