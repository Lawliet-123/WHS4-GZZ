cmake_minimum_required(VERSION 3.21)
project(ac_whistle CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Dumper-7 로 뽑은 CppSDK 가 필요하다.
#
# **SDK 는 이 저장소에 넣지 않는다.** 게임 바이너리에서 파생된 산출물이라
# 재배포하지 않는다는 규칙이다. 각자 Dumper-7 로 뽑아서 경로만 넘긴다.
#
#   cmake -B build -DSDK_DIR=C:/path/to/CppSDK
#
set(SDK_DIR "" CACHE PATH "Dumper-7 CppSDK 루트 (SDK/Basic.hpp 가 있는 폴더)")

if(NOT SDK_DIR OR NOT EXISTS "${SDK_DIR}/SDK/Basic.hpp")
    message(FATAL_ERROR
        "CppSDK 를 찾지 못했습니다 (SDK_DIR='${SDK_DIR}').\n"
        "  Dumper-7 로 MECCHA CHAMELEON 4.0.2 를 덤프한 뒤\n"
        "  cmake -B build -DSDK_DIR=<CppSDK 경로> 로 지정하세요.\n"
        "  절차: docs/18-dumper7-runbook.md")
endif()

set(SDK_SOURCES
    "${SDK_DIR}/SDK/Basic.cpp"
    "${SDK_DIR}/SDK/CoreUObject_functions.cpp"
    # Basic.cpp 의 GetStaticName() 이 UKismetStringLibrary::Conv_StringToName 을
    # 부른다. 이 파일을 빼면 LNK2019 가 난다.
    "${SDK_DIR}/SDK/Engine_functions.cpp"
)

add_library(ac_whistle SHARED src/main.cpp ${SDK_SOURCES})
target_include_directories(ac_whistle PRIVATE "${SDK_DIR}")

if(MSVC)
    # /utf-8  : 소스에 한글 주석이 있다. 없으면 cp949 로 읽어 C2001 이 난다.
    # /EHa    : __try/__except 를 C++ 예외와 섞으려면 필요하다.
    # /bigobj : SDK 헤더가 커서 오브젝트 섹션 상한에 걸린다.
    target_compile_options(ac_whistle PRIVATE /utf-8 /EHa /bigobj /permissive- /W3)
    target_compile_definitions(ac_whistle PRIVATE
        WIN32_LEAN_AND_MEAN NOMINMAX _CRT_SECURE_NO_WARNINGS)
endif()

# 주입된 DLL 은 매핑된 채로 남아 링커가 덮어쓰지 못한다(LNK1104).
# 코드를 고칠 때마다 이 숫자를 올려 새 파일명을 만든다.
set(AC_VERSION "1")

set_target_properties(ac_whistle PROPERTIES
    OUTPUT_NAME "ac_whistle_v${AC_VERSION}"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/bin")
