#!/usr/bin/python3

import os

class Config:
    def __init__(self,
               url_base,
               output_dir,
               template_dir,
               home_site,
               id_base,
               work_dir,
               page_output_path=None):
        self.url_base = url_base
        self.output_dir = output_dir and os.path.abspath(output_dir)
        if page_output_path:
            self.page_output_dir = os.path.join(self.output_dir,page_output_path)
        else:
            self.page_output_dir = self.output_dir
        self.template_dir = template_dir and os.path.abspath(template_dir)
        self.work_dir = work_dir
        self.home_site = home_site
        self.id_base = id_base
