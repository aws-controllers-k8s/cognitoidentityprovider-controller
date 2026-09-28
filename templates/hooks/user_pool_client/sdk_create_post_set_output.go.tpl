if desired.ko.Spec.ClientSecretExportTo != nil {
	if resp.UserPoolClient.ClientSecret == nil {
		return nil, ackerr.NewTerminalError(fmt.Errorf("Cognito did not return a generated client secret"))
	}
	ref := desired.ko.Spec.ClientSecretExportTo
	namespace := ref.Namespace
	if namespace == "" {
		namespace = desired.ko.Namespace
	}
	if err = rm.rr.WriteToSecret(ctx, *resp.UserPoolClient.ClientSecret, namespace, ref.Name, ref.Key); err != nil {
		return nil, err
	}
}
listResp, listErr := rm.sdkapi.ListUserPoolClientSecrets(ctx, &svcsdk.ListUserPoolClientSecretsInput{
    ClientId:   resp.UserPoolClient.ClientId,
    UserPoolId: resp.UserPoolClient.UserPoolId,
})
rm.metrics.RecordAPICall("READ_MANY", "ListUserPoolClientSecrets", listErr)
if listErr == nil && len(listResp.ClientSecrets) == 1 && listResp.ClientSecrets[0].ClientSecretId != nil {
    ko.Status.InitialClientSecretID = listResp.ClientSecrets[0].ClientSecretId
}
