cmake_minimum_required(VERSION 3.20)
project(camandroid_vcam CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(MSVC)
  # Static CRT: the DLL loads into arbitrary apps (Zoom, Teams, ...) that may not ship our runtime.
  set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
  add_compile_options(/W4 /permissive- /guard:cf)
  add_link_options(/guard:cf /DYNAMICBASE /NXCOMPAT)
endif()

if(CMAKE_SIZEOF_VOID_P EQUAL 8)
  set(VCAM_NAME camandroid-vcam64)
else()
  set(VCAM_NAME camandroid-vcam32)
endif()

add_library(vcam SHARED src/vcam.cpp src/vcam.def src/vcam.rc)
set_target_properties(vcam PROPERTIES OUTPUT_NAME ${VCAM_NAME})
target_compile_definitions(vcam PRIVATE UNICODE _UNICODE)
target_link_libraries(vcam PRIVATE strmiids ole32 oleaut32 uuid gdi32 advapi32)

add_executable(vcamtest test/vcamtest.cpp)
target_compile_definitions(vcamtest PRIVATE UNICODE _UNICODE)
target_link_libraries(vcamtest PRIVATE strmiids ole32 oleaut32 uuid)

# H.264 decoder for camandroid.exe (Media Foundation), loaded with ctypes.
add_library(h264dec SHARED src/h264dec.cpp)
set_target_properties(h264dec PROPERTIES OUTPUT_NAME camandroid-h264)
target_compile_definitions(h264dec PRIVATE UNICODE _UNICODE)
target_link_libraries(h264dec PRIVATE mfplat mfuuid ole32 uuid)
