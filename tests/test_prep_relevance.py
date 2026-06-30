from app.services.prep_retrieval import compute_relevance_score


def test_azure_job_description_ranks_azure_above_aws_question():
    jd = """
    Senior Azure Cloud Engineer. Azure App Service, AKS, Cosmos DB, Entra ID,
    ARM templates, Azure Functions, and .NET on Azure.
  """
    azure_score = compute_relevance_score(
        "How do you deploy and monitor applications on Azure App Service and AKS?",
        ["azure", "kubernetes"],
        jd,
        times_seen=1,
    )
    aws_score = compute_relevance_score(
        "Describe your experience designing highly available systems on AWS using ECS and Lambda.",
        ["aws", "cloud"],
        jd,
        times_seen=3,
    )
    assert azure_score > aws_score


def test_shared_keywords_boost_both_when_jd_is_cloud_generic():
    jd = "Cloud engineer role working with distributed systems and microservices."
    score = compute_relevance_score(
        "How would you design a resilient microservices architecture?",
        ["microservices"],
        jd,
        times_seen=1,
    )
    assert score > 0
