cmake_minimum_required(VERSION 3.15)
project(lians_c_sdk VERSION 0.3.0 LANGUAGES C)

set(CMAKE_C_STANDARD 99)
set(CMAKE_C_STANDARD_REQUIRED ON)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(CURL REQUIRED)

# ── Library ──────────────────────────────────────────────────────────────────
add_library(lians src/lians.c src/lians_json.c)
target_include_directories(lians
  PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}/include
  PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_link_libraries(lians PUBLIC CURL::libcurl)

if(MSVC)
  target_compile_options(lians PRIVATE /W3)
else()
  target_compile_options(lians PRIVATE -Wall -Wextra)
endif()

# ── Example (compiles; needs a running server + env vars to do anything) ─────
add_executable(lians_example examples/example.c)
target_link_libraries(lians_example PRIVATE lians)

# ── Tests (pure helpers — no network, no libcurl needed) ─────────────────────
enable_testing()
add_executable(lians_test_json tests/test_json.c src/lians_json.c)
target_include_directories(lians_test_json PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
add_test(NAME json_unit COMMAND lians_test_json)
