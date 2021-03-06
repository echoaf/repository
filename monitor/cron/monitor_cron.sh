#!/bin/bash

source ~/.bashrc

base_dir="/data/repository/monitor"
common_dir="$base_dir/common"
shell_cnf="$common_dir/shell.cnf"
cron_dir="$base_dir/cron"
source $shell_cnf

f_name=$(basename $0)

cd $cron_dir

sh 1_sec.sh &
sh 1_min.sh &
sh 5_min.sh &
sh 1_hour.sh &
sh 1_day.sh &
