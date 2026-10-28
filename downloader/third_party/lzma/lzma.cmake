# The LZMA SDK's 7z decoder (README.md): DL_LZMA_SOURCES and DL_LZMA_INCLUDE for ../../CMakeLists.txt and
# ../../tests/CMakeLists.txt. Compiled into each target (not a library), so each keeps its own C runtime setting.
set(DL_LZMA_INCLUDE ${CMAKE_CURRENT_LIST_DIR}/C)
set(DL_LZMA_SOURCES)
foreach(f 7zAlloc 7zArcIn 7zBuf 7zCrc 7zCrcOpt 7zDec 7zStream Bcj2 Bra Bra86 BraIA64 CpuArch Delta Lzma2Dec LzmaDec)
	list(APPEND DL_LZMA_SOURCES ${DL_LZMA_INCLUDE}/${f}.c)
endforeach()
if(MSVC)
	set_source_files_properties(${DL_LZMA_SOURCES} PROPERTIES COMPILE_OPTIONS "/W1")
else()
	set_source_files_properties(${DL_LZMA_SOURCES} PROPERTIES COMPILE_OPTIONS "-w")
endif()
