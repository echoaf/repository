#!/bin/bash


base_dir="/data/repo/repository/monitor"
common_dir="$base_dir/common"
shell_cnf="$common_dir/shell.cnf"
source $shell_cnf

f_name=$(basename $0)

collection_py_1_min=("mysql_status.py" "mysql_system_info.py" "system_info.py")

printLog "[$f_name]======开始执行1min python脚本======" "$normal_log"
cd $collection_path_1_min
for p in ${collection_py_1_min[@]}
do
	printLog "[$f_name]start $p" $normal_log
	python $p &
done
