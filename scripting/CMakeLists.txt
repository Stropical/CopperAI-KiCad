set( KIPYTHON_SRCS
    kipython_settings.cpp
)

add_library( scripting STATIC
    ${KIPYTHON_SRCS}
)

target_link_libraries( scripting
    ${wxWidgets_LIBRARIES}      # wxLogDebug, wxASSERT
    ${PYTHON_LIBRARIES}
    Boost::headers
    common
    kicommon
)

target_include_directories( scripting PUBLIC
    ${PYTHON_INCLUDE_DIRS}
    ${PROJECT_BINARY_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}
)

target_include_directories( scripting PRIVATE
    $<TARGET_PROPERTY:nlohmann_json,INTERFACE_INCLUDE_DIRECTORIES>
    ${PROJECT_SOURCE_DIR}/resources/bitmaps_png/include
    ${PROJECT_SOURCE_DIR}/include
    ${wxWidgets_LIBRARIES}
)

# Setup the KIFACE
add_library( scripting_kiface MODULE
    kicad_scripting_main.cpp
    kipython_frame.cpp
    ${KIPYTHON_SRCS}
    )

set_source_files_properties( kicad_scripting_main.cpp PROPERTIES
    # The KIFACE is in kicad_scripting_main.cpp, export it:
    COMPILE_DEFINITIONS     "BUILD_KIWAY_DLL;COMPILING_DLL"
    )

target_include_directories( scripting_kiface PRIVATE
    ${PROJECT_SOURCE_DIR}/resources/bitmaps_png/include
    ${PROJECT_SOURCE_DIR}/include
    ${wxWidgets_LIBRARIES}
)

if( MSVC )
    target_sources( scripting_kiface PRIVATE ${CMAKE_SOURCE_DIR}/resources/msw/kipython-dll.rc )
endif()

target_link_libraries( scripting_kiface
    scripting
    )

set_target_properties( scripting_kiface PROPERTIES
    OUTPUT_NAME     kipython
    PREFIX          ${KIFACE_PREFIX}
    SUFFIX          ${KIFACE_SUFFIX}
    )

target_link_options( scripting_kiface PRIVATE
    $<$<BOOL:${KICAD_MAKE_LINK_MAPS}>:-Wl,--cref,-Map=_scripting.kiface.map>
    )

if( APPLE )
    set_target_properties( scripting_kiface PROPERTIES
        LIBRARY_OUTPUT_DIRECTORY ${OSX_BUNDLE_BUILD_KIFACE_DIR}
        )
    set_target_properties( scripting_kiface PROPERTIES INSTALL_RPATH
            "@executable_path/../Frameworks;@executable_path/../Frameworks/Python.framework" )
    set_target_properties( scripting_kiface PROPERTIES BUILD_WITH_INSTALL_RPATH 1 )

    install( CODE "
        set( KICAD_CMAKE_MODULE_PATH \"${KICAD_CMAKE_MODULE_PATH}\" )
        set( OSX_BUNDLE_BUILD_KIFACE_DIR \"${OSX_BUNDLE_BUILD_KIFACE_DIR}\" )
        set( OSX_BUNDLE_INSTALL_LIB_DIR \"${OSX_BUNDLE_INSTALL_LIB_DIR}\" )

        include( ${KICAD_CMAKE_MODULE_PATH}/InstallSteps/InstallMacOS.cmake )

        # Install any dependencies
        install_runtime_deps( \"\"
            \"${OSX_BUNDLE_BUILD_KIFACE_DIR}/_kipython.kiface\"
            \"\"
            )
    " )
else()
    install( TARGETS scripting_kiface
        DESTINATION ${KICAD_KIFACE}
        COMPONENT binary
        )
endif()

if( KICAD_WIN32_INSTALL_PDBS )
    # Get the PDBs to copy over for MSVC
    install(FILES $<TARGET_PDB_FILE:scripting_kiface> DESTINATION ${KICAD_KIFACE})
endif()

# python shell installation
install( DIRECTORY ${PROJECT_SOURCE_DIR}/scripting/kicad_pyshell/
    DESTINATION ${KICAD_DATA}/scripting/kicad_pyshell
    FILE_PERMISSIONS OWNER_EXECUTE OWNER_READ OWNER_WRITE GROUP_EXECUTE GROUP_READ WORLD_EXECUTE WORLD_READ
)

# Install IPC API Python module
install( FILES ${PROJECT_SOURCE_DIR}/scripting/kicad_ipc_api.py
    DESTINATION ${KICAD_DATA}/scripting
    COMPONENT binary
)

# Install Python IPC scripts to each individual application's Resources directory
# This ensures the scripts are found when running standalone apps (e.g., eeschema.app)
if( APPLE )
    # On macOS: Install to each .app bundle's Contents/Resources/scripting
    set( KICAD_APPS eeschema pcbnew gerbview pl_editor bitmap2component pcb_calculator )
    foreach( APP ${KICAD_APPS} )
        install( FILES 
            ${PROJECT_SOURCE_DIR}/scripting/kicad_ipc_api.py
            DESTINATION ${OSX_BUNDLE_INSTALL_BIN_DIR}/../Applications/${APP}.app/Contents/Resources/scripting
            COMPONENT binary
        )
    endforeach()
elseif( WIN32 )
    # On Windows: Install to bin/scripting for each executable to find
    install( FILES 
        ${PROJECT_SOURCE_DIR}/scripting/kicad_ipc_api.py
        DESTINATION ${KICAD_BIN}/scripting
        COMPONENT binary
    )
else()
    # On Linux: Already installed to ${KICAD_DATA}/scripting above
    # Add kicad_ipc_api.py here too for consistency
endif()

