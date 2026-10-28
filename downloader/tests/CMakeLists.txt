# Unit tests of the downloader's portable core (no Windows headers). Included by ../CMakeLists.txt and buildable on its
# own, as CI can do on Linux: cmake -S downloader/tests -B build/downloader-tests && cmake --build build/downloader-tests
cmake_minimum_required(VERSION 3.20)
if(NOT CMAKE_PROJECT_NAME)
	project(downloader_tests LANGUAGES C CXX)
endif()

include(${CMAKE_CURRENT_LIST_DIR}/../third_party/lzma/lzma.cmake)

set(core ${CMAKE_CURRENT_LIST_DIR}/../src/core)
add_executable(downloader_tests core_tests.cpp ${core}/sha256.cpp ${core}/json.cpp ${core}/manifest.cpp ${core}/detect.cpp
               ${core}/paths.cpp ${core}/sevenzip.cpp ${DL_LZMA_SOURCES})
target_compile_definitions(downloader_tests PRIVATE DL_RES_DIR="${CMAKE_CURRENT_LIST_DIR}/../res"
                           DL_TEST_DATA="${CMAKE_CURRENT_LIST_DIR}/data")
if(NOT DL_CONFIG_HEADER)
	set(DL_CONFIG_HEADER "${CMAKE_CURRENT_LIST_DIR}/../config/downloader_config.h")
endif()
target_compile_definitions(downloader_tests PRIVATE DL_CONFIG_HEADER_FILE="${DL_CONFIG_HEADER}" DL_VERSION="0.0.0")
target_compile_features(downloader_tests PRIVATE cxx_std_20)
target_include_directories(downloader_tests PRIVATE ${DL_LZMA_INCLUDE})
if(MSVC)
	target_compile_options(downloader_tests PRIVATE $<$<COMPILE_LANGUAGE:CXX>:/W4 /permissive- /EHsc> /utf-8)
else()
	target_compile_options(downloader_tests PRIVATE $<$<COMPILE_LANGUAGE:CXX>:-Wall -Wextra>)
endif()
