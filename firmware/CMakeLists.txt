# Scorbot ER-4u controller firmware. Build with ESP-IDF 5.3+:
#   idf.py set-target esp32 && idf.py build flash monitor
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(scorbot_controller)
