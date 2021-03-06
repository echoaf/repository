#!/bin/bash

# 清理日志


base_dir="/data/repository/monitor"
common_dir="$base_dir/common"
sec_dir="$base_dir/collection/1_sec"
source $common_dir/shell.cnf

f_name=$(basename "$0")
log_large=$(getKV "log_large" "$local_ip" "0" "system")   


function logRatate()
{

    yesterday=$(date +%Y%m%d --date="-1 day") 
    today=$(date +%Y%m%d) 
    backup_path="$log_dir/backup/$yesterday"
    mkdir -p $log_dir/backup

    cd $log_dir/backup
    if [ -d "$backup_path" ] || [ -f "${backup_path}.tar.gz" ];then
        echo "已经存在了需要备份到的日志目录${yesterday},请先清理,exit"
        exit 64
    else
        mkdir -p $backup_path
    fi

    # 处理日志文件
    logs=$(find $log_dir/ -maxdepth 1 -name "*.log") # 日志都是.log后缀
    for log in $logs
    do
        base_log=$(basename $log)
        /bin/cp -avf $log ${backup_path}/${base_log}_${yesterday}_${today} >>$normal_log 2>&1
        echo "" > $log # 清空日志,不能使用rm
    done

    # 处理日志目录
    cd $log_dir
    dirs=$(find . -maxdepth 1 -type d | sed 1d | grep -Evw "backup")
    for dir in $(echo "$dirs")
    do
        base_dir=$(basename $dir)
        mkdir -p $backup_path/$base_dir
        cd $log_dir && mv -vf $base_dir/* $backup_path/$base_dir/ >>$normal_log 2>&1
    done
    
    # --remove-files:压缩后删除源文件
    cd $log_dir/backup && tar -zcvf ${yesterday}.tar.gz $yesterday --remove-files 

    #cd $log_dir/backup && find $log_dir/backup -type d | xargs -i rm -rfv {} # 可能是解压查看日志忘记删除
    cd $log_dir/backup && find $log_dir/backup -type f -name "*.tar.gz" -atime +${log_large} | xargs -i rm -rfv {} # 删除log_large之前的压缩包

}


function main()
{
    logRatate
}

main
