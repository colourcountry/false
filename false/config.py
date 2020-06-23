#!/usr/bin/python3

import os

class Config:
    def __init__(self,
               url_base,
               output_dir,
               template_dir,
               home_site,
               id_base,
               work_dir):
        self.url_base = url_base
        self.output_dir = output_dir and os.path.abspath(output_dir)
        self.template_dir = template_dir and os.path.abspath(template_dir)
        self.work_dir = work_dir
        self.home_site = home_site
        self.id_base = id_base
