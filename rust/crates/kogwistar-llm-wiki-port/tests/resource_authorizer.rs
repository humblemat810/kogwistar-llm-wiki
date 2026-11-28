use kogwistar_llm_wiki_port::configuration::resource_authorizer::{
    parse_resource_authorizer, resource_authorizer_from_env_vars, ResourceAuthorizerSpec,
};

#[test]
fn missing_configuration_returns_none() {
    assert_eq!(parse_resource_authorizer("").unwrap(), None);
    assert_eq!(parse_resource_authorizer("   ").unwrap(), None);
}

#[test]
fn explicit_module_callable_spec_is_parsed() {
    assert_eq!(
        parse_resource_authorizer(" app.policy : authorize ").unwrap(),
        Some(ResourceAuthorizerSpec {
            module_name: "app.policy".to_string(),
            attribute: "authorize".to_string(),
        })
    );
}

#[test]
fn invalid_spec_error_matches_the_python_contract() {
    for specification in [
        "module",
        "module:",
        ":callable",
        "module:a:b",
        " :authorize",
    ] {
        let error = parse_resource_authorizer(specification)
            .unwrap_err()
            .to_string();

        assert_eq!(
            error, "LLM_WIKI_RESOURCE_AUTHORIZER must be module:callable",
            "unexpected error for {specification:?}"
        );
    }
}

#[test]
fn environment_settings_are_read_with_the_python_key() {
    let env = [("LLM_WIKI_RESOURCE_AUTHORIZER", " app.policy:authorize ")];

    assert_eq!(
        resource_authorizer_from_env_vars(env).unwrap(),
        Some(ResourceAuthorizerSpec {
            module_name: "app.policy".to_string(),
            attribute: "authorize".to_string(),
        })
    );
}
