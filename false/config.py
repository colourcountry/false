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
               page_file_type,
               page_output_path):

        self.set(url_base,
               output_dir,
               template_dir,
               home_site,
               id_base,
               work_dir,
               page_file_type,
               page_output_path)

    def set(self,
               url_base=None,
               output_dir=None,
               template_dir=None,
               home_site=None,
               id_base=None,
               work_dir=None,
               page_file_type=None,
               page_output_path=None):
        self.url_base = url_base or self.url_base
        self.output_dir = (output_dir and os.path.abspath(output_dir)) or self.output_dir
        self.page_output_path = page_output_path or self.page_output_path
        if self.page_output_path:
            self.page_output_dir = os.path.join(self.output_dir,self.page_output_path)
        else:
            self.page_output_dir = self.output_dir
        self.template_dir = (template_dir and os.path.abspath(template_dir)) or self.template_dir
        self.work_dir = work_dir or self.work_dir
        self.home_site = home_site or self.home_site
        self.id_base = id_base or self.id_base
        self.page_file_type = page_file_type or self.page_file_type
        self.html_escape = self.page_file_type=="html"
