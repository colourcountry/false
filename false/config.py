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
        self.output_dir = os.path.abspath(output_dir)
        self.template_dir = os.path.abspath(template_dir)
        self.home_site = home_site
        self.id_base = id_base

    def setIPFS(self, ipfs_client, ipfs_namespace, ipfs_dir, ipfs_cache_dir=None):
        self.ipfs_client = ipfs_client
        self.ipfs_namespace = ipfs_namespace
        self.ipfs_dir = os.path.join(self.output_dir, ipfs_dir)
        if ipfs_cache_dir is None: 
            self.ipfs_cache_dir = None
        else:
            self.ipfs_cache_dir = os.path.abspath(ipfs_cache_dir)

    def validate(self):
        pass # TODO
